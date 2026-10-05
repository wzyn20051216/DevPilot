"""第十四至十六关 Evals 基础设施测试。

这些测试不调用 LLM，专门覆盖数据加载、Workspace 隔离、事件计数和 SQLite
序列化，保证昂贵的四组实验开始前基础设施已经可靠。
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from backend.src.database import connection
from backend.src.database.evaluation_repository import evaluation_repository
from research.evals import exporter, plotter
from research.evals.analysis import (
    aggregate_case_results,
    bootstrap_mean_ci,
    build_ablation_report,
    get_paired_deltas,
    summarize_by_difficulty,
    wilcoxon_paired_test,
)
from research.evals.audit import build_audit_report
from research.evals.dataset import (
    calculate_dataset_fingerprint,
    get_fixture_path,
    load_benchmark_cases,
    load_case,
)
from research.evals.metrics import summarize_results
from research.evals.models import BenchmarkCase, EvaluationResult, EvaluationVariant
from research.evals.real_world import (
    SweBenchInstance,
    _candidate_paths,
    _failed_pytest_nodes,
    _protected_candidate_paths,
    _repeat_suffix,
    select_stratified_instances,
)
from research.evals.retrieval import score_retrieval
from research.evals.runner import (
    VARIANTS,
    collect_event_metrics,
    collect_usage,
    create_workspace,
    prepare_verification_workspace,
)
from backend.src.main import app
from backend.src.models.agent_state import AgentEvent
from backend.src.tools.write_tool import use_write_guard, write_file


def test_load_add_bug_case() -> None:
    """add_bug 用例及其 fixture 应能被正常加载。"""

    case = load_case("add_bug")
    assert case.repo_fixture == "add_bug_repo"
    assert case.verification_target == "tests/test_calculator.py"
    assert case.difficulty == "easy"
    assert case.category == "bugfix"
    assert "single_file" in case.tags


def test_dataset_covers_all_difficulty_levels() -> None:
    """正式实验数据集应覆盖三档难度，且每档不少于初始 3 个用例。"""

    cases = load_benchmark_cases()
    assert {case.difficulty for case in cases} == {"easy", "medium", "hard"}
    # 跨语言扩展（10.2.7）后用例总数从 9 增至 12，难度分布变为 3/5/4；
    # 这里改为动态下限断言，新增 fixture 时无需再改本测试。
    assert len(cases) == 12
    assert all(
        sum(item.difficulty == level for item in cases) >= 3
        for level in ("easy", "medium", "hard")
    )
    assert all(case.tags for case in cases)
    assert all(case.retrieval_queries for case in cases)
    assert len(calculate_dataset_fingerprint()) == 64


def test_retrieval_metrics_use_unique_files_and_first_relevant_rank() -> None:
    """Recall@K 和 MRR 应按文件去重并使用首个相关文件排名。"""

    recall, reciprocal_rank = score_retrieval(
        ["noise.py", "target.py", "target.py", "other.py"],
        ["target.py", "second.py"],
    )

    assert recall == 0.5
    assert reciprocal_rank == 0.5


def test_real_world_verifier_protects_tests_and_test_config() -> None:
    """真实评测不得接受候选对测试和 pytest 配置的修改。"""

    assert _protected_candidate_paths(
        [
            "src/package.py",
            "tests/test_package.py",
            "package/tests/helpers.py",
            "src/module_test.py",
            "setup.cfg",
        ]
    ) == [
        "tests/test_package.py",
        "package/tests/helpers.py",
        "src/module_test.py",
        "setup.cfg",
    ]


def test_real_world_write_guard_blocks_test_edits(tmp_path: Path) -> None:
    """! @brief 评测期间测试文件不可被改写，离开上下文后普通写入恢复。"""

    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    test_file = test_dir / "test_example.py"
    test_file.write_text("assert False\n", encoding="utf-8")
    source_file = tmp_path / "source.py"
    source_file.write_text("old\n", encoding="utf-8")

    with use_write_guard(lambda path: bool(_protected_candidate_paths([path]))):
        with pytest.raises(ValueError, match="禁止修改测试"):
            write_file(str(tmp_path), "tests/test_example.py", "assert True\n")
        write_file(str(tmp_path), "source.py", "new\n")

    assert test_file.read_text(encoding="utf-8") == "assert False\n"
    assert source_file.read_text(encoding="utf-8") == "new\n"
    write_file(str(tmp_path), "tests/test_example.py", "assert True\n")


def test_real_world_audit_extracts_unstable_pytest_nodes() -> None:
    """环境基线校准应提取 pytest 摘要中的明确失败节点，并归一化参数化后缀。

    参数化测试在失败摘要里带 ``[参数]`` 后缀，而 SWE-bench 的 FAIL_TO_PASS
    存的是不带后缀的节点名（甚至被换行截断成 ``...[\n``）。若不归一化，
    ``issubset`` 匹配会失败（astroid-1866 实测）。
    """

    assert _failed_pytest_nodes(
        {
            "stdout": (
                "FAILED tests/test_a.py::test_one - AssertionError\n"
                "FAILED tests/test_b.py::test_value[hello world] - ValueError\n"
                "ERROR tests/test_c.py::test_setup - RuntimeError\n"
                "2 failed, 1 error, 3 passed\n"
            )
        }
    ) == [
        "tests/test_a.py::test_one",
        "tests/test_b.py::test_value",
        "tests/test_c.py::test_setup",
    ]


def test_real_world_candidate_patch_includes_untracked_source(tmp_path: Path) -> None:
    """真实评测必须把 Agent 新建的源码纳入候选补丁。"""

    import subprocess

    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    existing = tmp_path / "existing.py"
    existing.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=DevPilot Test",
            "-c",
            "user.email=test@devpilot.local",
            "commit",
            "--quiet",
            "-m",
            "base",
        ],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "new_module.py").write_text("VALUE = 2\n", encoding="utf-8")

    assert _candidate_paths(tmp_path) == ["new_module.py"]
    patch = subprocess.run(
        ["git", "diff", "--binary", "HEAD", "--", "new_module.py"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "new file mode" in patch
    assert "+VALUE = 2" in patch


def test_real_world_invalid_environment_persists_diagnostics(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """金补丁也无法通过时，必须在调用 Agent 前留下可诊断报告并跳过该实例。

    无效环境不应终止整批评测（否则 5 个 pvlib 的 np.Inf 会拖垮 20 实例），
    而是如实记入报告、跳过不进入成功率分母，其余有效实例继续跑。
    """

    from research.evals import real_world

    instance = real_world.SweBenchInstance(
        instance_id="owner__repo-1",
        repo="owner/repo",
        base_commit="abc",
        problem_statement="fix",
        gold_patch="",
        test_patch="",
        fail_to_pass=("tests/test_bug.py::test_bug",),
        pass_to_pass=(),
    )
    audit = {
        "instance_id": instance.instance_id,
        "baseline_failed": False,
        "gold_passed": False,
        "baseline_result": {"returncode": 4, "stderr": "import failed"},
        "gold_result": {"returncode": 4, "stderr": "import failed"},
    }
    monkeypatch.setattr(real_world, "REAL_EVAL_ROOT", tmp_path)
    monkeypatch.setattr(
        real_world,
        "load_swebench_lite_dev",
        lambda: {instance.instance_id: instance},
    )
    monkeypatch.setattr(real_world, "audit_real_instance", lambda *_: audit)
    monkeypatch.setattr(
        real_world,
        "evaluate_real_instance",
        lambda *_args, **_kwargs: pytest.fail("无效环境不得调用 Agent"),
    )

    # 无效环境不再抛 RuntimeError，而是正常返回并跳过。
    report_path = real_world.run_real_world_evaluation(
        instance_ids=(instance.instance_id,),
        variants=("single_no_rag",),
    )

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["status"] in {"partial_invalid_environment", "completed"}
    assert payload["rows"] == []
    assert payload["audits"][0]["gold_result"]["stderr"] == "import failed"


def test_all_benchmark_baselines_fail_before_agent_changes() -> None:
    """每个错误版 fixture 的 verifier 都必须先失败，防止无效样本混入实验。"""

    report = build_audit_report(execute=True)
    assert report["valid"] is True
    assert report["case_count"] == len(load_benchmark_cases())
    assert all(row["baseline_failed"] is True for row in report["cases"])


def test_variants_use_independent_git_workspaces(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """不同 variant 必须得到互不影响且保留 .git 的仓库副本。"""

    from research.evals import runner

    monkeypatch.setattr(runner, "EVAL_WORKSPACE_ROOT", tmp_path)
    case = load_case("add_bug")
    first = create_workspace(case, "single_no_rag", "test-run")
    second = create_workspace(case, "multi_rag", "test-run")

    assert first != second
    assert (first / ".git").is_dir()
    assert (second / ".git").is_dir()
    (first / "calculator.py").write_text("changed", encoding="utf-8")
    assert (second / "calculator.py").read_text(encoding="utf-8") != "changed"


def test_repeats_use_independent_workspaces(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """同一 case/variant 的重复实验不能覆盖上一轮 Git Workspace。"""

    from research.evals import runner

    monkeypatch.setattr(runner, "EVAL_WORKSPACE_ROOT", tmp_path)
    case = load_case("add_bug")
    first = create_workspace(case, "single_no_rag", "test-run", repeat_index=1)
    second = create_workspace(case, "single_no_rag", "test-run", repeat_index=2)

    assert first != second
    assert "repeat-1" in first.parts
    assert "repeat-2" in second.parts
    assert (first / ".git").is_dir()
    assert (second / ".git").is_dir()


def test_verifier_rejects_test_and_config_tampering(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """候选不得通过修改测试或 pytest 配置骗过独立裁判。"""

    from research.evals import runner

    monkeypatch.setattr(runner, "EVAL_WORKSPACE_ROOT", tmp_path)
    case = load_case("add_bug")
    workspace = create_workspace(case, "single_no_rag", "tamper-test")
    test_file = workspace / "tests" / "test_calculator.py"
    test_file.write_text("def test_fake():\n    assert True\n", encoding="utf-8")

    with pytest.raises(ValueError, match="裁判保护路径"):
        prepare_verification_workspace(case, workspace)


def test_verifier_applies_only_candidate_source_changes(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """干净裁判副本应包含候选源码，并保留基线测试。"""

    from research.evals import runner

    monkeypatch.setattr(runner, "EVAL_WORKSPACE_ROOT", tmp_path)
    case = load_case("add_bug")
    workspace = create_workspace(case, "single_no_rag", "clean-verifier")
    calculator = workspace / "calculator.py"
    calculator.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    verifier = prepare_verification_workspace(case, workspace)

    assert "a + b" in (verifier / "calculator.py").read_text(encoding="utf-8")
    assert (verifier / "tests" / "test_calculator.py").read_text(encoding="utf-8") == (
        get_fixture_path(case) / "tests" / "test_calculator.py"
    ).read_text(encoding="utf-8")


def test_event_metrics_and_usage_are_aggregated() -> None:
    """多 Agent 的迭代和 Token 应求总和，而不是只取单个角色最大值。"""

    events = [
        AgentEvent(type="thinking", agent="planner", iteration=1, message=""),
        AgentEvent(type="tool_call", agent="planner", iteration=1, message=""),
        AgentEvent(
            type="final",
            agent="planner",
            message="",
            data={"usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}},
        ),
        AgentEvent(type="thinking", agent="coder", iteration=1, message=""),
        AgentEvent(
            type="final",
            agent="coder",
            message="",
            data={
                "repair_rounds": 1,
                "usage": {"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23},
            },
        ),
        AgentEvent(
            type="cancelled",
            agent="reviewer",
            message="cancelled",
            data={
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 1,
                    "total_tokens": 6,
                }
            },
        ),
    ]

    assert collect_event_metrics(events) == (1, 2, 1)
    assert collect_usage(events) == (35, 6, 41)


def test_summary_groups_variants() -> None:
    """汇总器应分别计算各 variant 的指标。"""

    results = [
        EvaluationResult(
            run_id="run",
            case_id="case",
            variant="single_no_rag",
            success=True,
            tests_passed=True,
            tool_calls=2,
            iterations=3,
            elapsed_seconds=1.5,
            total_tokens=100,
            workspace_path="workspace-a",
        ),
        EvaluationResult(
            run_id="run",
            case_id="case",
            variant="single_no_rag",
            success=False,
            tests_passed=True,
            tool_calls=4,
            iterations=5,
            elapsed_seconds=2.5,
            total_tokens=200,
            workspace_path="workspace-b",
        ),
    ]

    summary = summarize_results(results)["single_no_rag"]
    assert summary["success_rate"] == 0.5
    assert summary["test_pass_rate"] == 1.0
    assert summary["avg_tool_calls"] == 3
    assert summary["avg_total_tokens"] == 150


def _evaluation_result(
    case_id: str,
    variant: EvaluationVariant,
    *,
    success: bool = True,
    tool_calls: int = 2,
    repeat: int = 1,
) -> EvaluationResult:
    """构造不触发 LLM 的统计测试样本。"""

    return EvaluationResult(
        run_id="analysis-run",
        case_id=case_id,
        variant=variant,
        success=success,
        tests_passed=success,
        tool_calls=tool_calls,
        iterations=tool_calls + 1,
        repair_rounds=0,
        elapsed_seconds=float(tool_calls),
        total_tokens=tool_calls * 100,
        workspace_path=f"workspace-{case_id}-{variant}-{repeat}",
    )


def test_case_level_analysis_and_ablation_report() -> None:
    """重复实验应先按 case 聚合，再产出 20 行配对消融结果。"""

    results: list[EvaluationResult] = []
    for case_id in ("add_bug", "config_timeout", "request_timeout"):
        for variant_index, variant in enumerate(VARIANTS, start=1):
            results.append(
                _evaluation_result(
                    case_id,
                    variant,
                    tool_calls=variant_index,
                    repeat=1,
                )
            )
            results.append(
                _evaluation_result(
                    case_id,
                    variant,
                    tool_calls=variant_index + 2,
                    repeat=2,
                )
            )

    aggregated = aggregate_case_results(results)
    assert len(aggregated) == 12
    assert aggregated[("add_bug", "single_no_rag")]["runs"] == 2
    assert aggregated[("add_bug", "single_no_rag")]["tool_calls"] == 2

    # B-A 的方向固定：single_rag 平均比 single_no_rag 多一次工具调用。
    assert get_paired_deltas(
        aggregated,
        "single_no_rag",
        "single_rag",
        "tool_calls",
    ) == [1.0, 1.0, 1.0]

    difficulty_rows = summarize_by_difficulty(results, load_benchmark_cases())
    assert len(difficulty_rows) == 12
    assert {row["difficulty"] for row in difficulty_rows} == {
        "easy",
        "medium",
        "hard",
    }
    assert all(row["runs"] == 2 for row in difficulty_rows)

    report = build_ablation_report(aggregated)
    assert len(report) == 20
    assert all(row["cases"] == 3 for row in report)


def test_statistical_helpers_are_deterministic_and_guard_small_samples() -> None:
    """Bootstrap 应可复现，小样本不应伪装成显著性结论。"""

    first = bootstrap_mean_ci([1.0, 2.0, 3.0], samples=2_000, seed=7)
    second = bootstrap_mean_ci([1.0, 2.0, 3.0], samples=2_000, seed=7)
    assert first == second
    assert first[0] <= 2.0 <= first[1]
    assert wilcoxon_paired_test([1.0, 1.0, 1.0, 1.0]) is None
    assert wilcoxon_paired_test([0.0] * 5) == 1.0


def test_full_experiment_uses_one_run_id_and_saves_config(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """三档用例、四种 variant、两次重复必须共享同一批次 ID。"""

    from research.evals import runner

    calls: list[tuple[str, EvaluationVariant, str, int, bool]] = []

    def fake_evaluate_case(
        case: BenchmarkCase,
        variant: EvaluationVariant,
        run_id: str | None = None,
        repeat_index: int = 1,
        persist: bool = True,
    ) -> EvaluationResult:
        assert run_id is not None
        calls.append((case.id, variant, run_id, repeat_index, persist))
        result = _evaluation_result(case.id, variant)
        result.repeat_index = repeat_index
        return result

    monkeypatch.setattr(runner, "EXPERIMENT_ROOT", tmp_path / "experiments")
    monkeypatch.setattr(runner, "evaluate_case", fake_evaluate_case)
    monkeypatch.setattr(
        runner,
        "collect_git_provenance",
        lambda: {
            "git_commit": "a" * 40,
            "git_dirty": False,
            "dirty_paths": [],
        },
    )

    run_id = runner.run_full_experiment(repeats=2)
    assert len(calls) == len(load_benchmark_cases()) * len(VARIANTS) * 2
    assert {call[2] for call in calls} == {run_id}
    assert {call[3] for call in calls} == {1, 2}
    assert all(call[4] is True for call in calls)

    config_path = tmp_path / "experiments" / run_id / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["repeats"] == 2
    assert config["benchmark_cases"] == len(load_benchmark_cases())
    assert len(config["dataset_sha256"]) == 64
    assert len(config["implementation_sha256"]) == 64
    assert len(config["git_commit"]) == 40
    assert config["git_dirty"] is False
    assert config["dirty_paths"] == []
    assert config["random_seed"] == 42
    assert config["variants"] == list(VARIANTS)
    assert config["variant_design"]["single_no_rag"] == {
        "architecture": "single_agent",
        "rag_enabled": False,
        "strategy": "static_baseline",
    }
    assert config["variant_design"]["multi_rag"]["rag_enabled"] is True
    assert config["experiment_strategy"] == "fixed_ablation"
    assert config["production_defaults"]["execution_mode"] == "multi_rag"
    assert config["verification"]["truth_source"] == (
        "independent_sandbox_verifier"
    )
    assert config["verification"]["success_rule"] == (
        "tests_passed_and_no_agent_error"
    )
    assert config["agent_recent_messages"] >= 4
    assert config["tool_observation_max_chars"] >= 2_000


def test_full_experiment_resume_skips_persisted_combinations(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """断点续跑不得重复执行已经写入 SQLite 的付费组合。"""

    from research.evals import runner

    calls: list[tuple[str, EvaluationVariant, int]] = []

    def fake_evaluate_case(
        case: BenchmarkCase,
        variant: EvaluationVariant,
        run_id: str | None = None,
        repeat_index: int = 1,
        persist: bool = True,
    ) -> EvaluationResult:
        assert run_id is not None
        calls.append((case.id, variant, repeat_index))
        result = _evaluation_result(case.id, variant)
        result.run_id = run_id
        result.repeat_index = repeat_index
        if persist:
            evaluation_repository.save_result(result)
        return result

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "resume.db")
    monkeypatch.setattr(runner, "EXPERIMENT_ROOT", tmp_path / "experiments")
    monkeypatch.setattr(runner, "evaluate_case", fake_evaluate_case)

    run_id = runner.run_full_experiment(repeats=1)
    assert len(calls) == len(load_benchmark_cases()) * len(VARIANTS)

    calls.clear()
    resumed_run_id = runner.run_full_experiment(repeats=1, run_id=run_id)
    assert resumed_run_id == run_id
    assert calls == []


def test_filtered_experiment_records_design_and_runs_requested_matrix(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """定向回归只应运行指定的 4 cases x 2 variants x 3 repeats。"""

    from research.evals import runner

    calls: list[tuple[str, EvaluationVariant, int]] = []

    def fake_evaluate_case(
        case: BenchmarkCase,
        variant: EvaluationVariant,
        run_id: str | None = None,
        repeat_index: int = 1,
        persist: bool = True,
    ) -> EvaluationResult:
        assert run_id is not None
        calls.append((case.id, variant, repeat_index))
        result = _evaluation_result(case.id, variant)
        result.run_id = run_id
        result.repeat_index = repeat_index
        return result

    case_ids = [
        "pagination_options",
        "order_total",
        "request_timeout",
        "cache_expiration",
    ]
    variants: tuple[EvaluationVariant, ...] = ("multi_no_rag", "multi_rag")
    monkeypatch.setattr(runner, "EXPERIMENT_ROOT", tmp_path / "experiments")
    monkeypatch.setattr(runner, "evaluate_case", fake_evaluate_case)

    run_id = runner.run_full_experiment(
        repeats=3,
        case_ids=case_ids,
        variants=variants,
    )

    assert len(calls) == 24
    assert {call[0] for call in calls} == set(case_ids)
    assert {call[1] for call in calls} == set(variants)
    assert {call[2] for call in calls} == {1, 2, 3}
    config = json.loads(
        (tmp_path / "experiments" / run_id / "config.json").read_text(
            encoding="utf-8"
        )
    )
    assert config["case_ids"] == case_ids
    assert config["variants"] == list(variants)
    assert config["repeats"] == 3


def test_filtered_experiment_resume_uses_saved_design(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """断点续跑省略筛选参数时，必须沿用首次保存的实验矩阵。"""

    from research.evals import runner

    calls: list[tuple[str, EvaluationVariant, int]] = []

    def fake_evaluate_case(
        case: BenchmarkCase,
        variant: EvaluationVariant,
        run_id: str | None = None,
        repeat_index: int = 1,
        persist: bool = True,
    ) -> EvaluationResult:
        assert run_id is not None
        calls.append((case.id, variant, repeat_index))
        result = _evaluation_result(case.id, variant)
        result.run_id = run_id
        result.repeat_index = repeat_index
        if persist:
            evaluation_repository.save_result(result)
        return result

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "filtered-resume.db")
    monkeypatch.setattr(runner, "EXPERIMENT_ROOT", tmp_path / "experiments")
    monkeypatch.setattr(runner, "evaluate_case", fake_evaluate_case)

    run_id = runner.run_full_experiment(
        repeats=1,
        case_ids=["request_timeout"],
        variants=["multi_no_rag"],
    )
    assert calls == [("request_timeout", "multi_no_rag", 1)]

    calls.clear()
    runner.run_full_experiment(repeats=None, run_id=run_id)
    assert calls == []


def test_repository_export_figures_and_summary_api(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """SQLite、CSV、PNG 和 Dashboard API 应形成一条完整数据链。"""

    database_path = tmp_path / "devpilot.db"
    monkeypatch.setattr(connection, "DATABASE_PATH", database_path)
    monkeypatch.setattr(exporter, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(plotter, "FIGURE_DIR", tmp_path / "figures")
    connection.init_database()

    result = EvaluationResult(
        run_id="dashboard-run",
        case_id="add_bug",
        variant="single_no_rag",
        success=True,
        tests_passed=True,
        tool_calls=7,
        iterations=5,
        elapsed_seconds=10.5,
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        workspace_path="workspace",
    )
    evaluation_repository.save_result(result)
    # 另存一套完整四 variant 数据，验证难度与消融 API。
    for variant in VARIANTS:
        evaluation_repository.save_result(
            _evaluation_result(
                "add_bug",
                variant,
                tool_calls=3,
            )
        )

    stored = evaluation_repository.get_results(run_id="dashboard-run")
    assert len(stored) == 1
    assert stored[0].tests_passed is True

    csv_path = exporter.export_results_csv(run_id="dashboard-run")
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf")
    exported = csv_path.read_text(encoding="utf-8-sig")
    assert "tests_passed" in exported
    assert "repeat_index" in exported

    figure_paths = plotter.generate_all_figures(run_id="dashboard-run")
    assert len(figure_paths) == 4
    assert all(path.is_file() and path.stat().st_size > 0 for path in figure_paths)

    response = TestClient(app).get(
        "/api/evals/summary",
    )
    assert response.status_code == 200
    assert response.json()["single_adaptive"]["runs"] == 18
    assert response.json()["single_adaptive"]["test_pass_rate"] == 14 / 18
    assert TestClient(app).get("/api/evals/summary", params={"run_id": "dashboard-run"}).status_code == 422

    difficulty_response = TestClient(app).get(
        "/api/evals/difficulty",
    )
    assert difficulty_response.status_code == 200
    assert difficulty_response.json() == []
    # 研究工具保留难度与消融验证，发布页面仅展示最终批次。
    analysis_results = evaluation_repository.get_results(run_id="analysis-run")
    assert len(summarize_by_difficulty(analysis_results, load_benchmark_cases())) == 4

    ablation_response = TestClient(app).get(
        "/api/evals/ablation",
    )
    assert ablation_response.status_code == 200
    assert ablation_response.json() == []
    assert len(build_ablation_report(aggregate_case_results(analysis_results))) == 20


def _fake_swebench_dataset(
    num_repos: int = 6,
    per_repo: int = 4,
) -> dict[str, SweBenchInstance]:
    """构造跨多个仓库、不触发网络/LLM/Docker 的假 dev split。"""

    dataset: dict[str, SweBenchInstance] = {}
    for repo_index in range(num_repos):
        repo = f"org{repo_index}/proj{repo_index}"
        for number in range(1, per_repo + 1):
            instance_id = f"org{repo_index}__proj{repo_index}-{number}"
            dataset[instance_id] = SweBenchInstance(
                instance_id=instance_id,
                repo=repo,
                base_commit="abc123",
                problem_statement="修复缺陷",
                gold_patch="",
                test_patch="",
                fail_to_pass=("tests/test_bug.py::test_bug",),
                pass_to_pass=(),
            )
    return dataset


def test_repeat_suffix_keeps_first_and_suffixes_later() -> None:
    """第 1 轮沿用历史命名，后续轮次追加 __r{n} 后缀。"""

    assert _repeat_suffix("single_no_rag", 1) == "single_no_rag"
    assert _repeat_suffix("single_no_rag", 2) == "single_no_rag__r2"
    assert _repeat_suffix("multi_rag", 3) == "multi_rag__r3"


def test_stratified_selection_is_deterministic_and_balanced() -> None:
    """分层采样应可复现，并优先覆盖尽可能多的仓库。"""

    dataset = _fake_swebench_dataset()

    assert select_stratified_instances(dataset, 10, seed=7) == (
        select_stratified_instances(dataset, 10, seed=7)
    )

    selected = select_stratified_instances(dataset, 10, seed=0)
    assert len(selected) == 10
    # 10 个实例来自 6 个仓库，round-robin 应覆盖全部仓库。
    assert {dataset[iid].repo for iid in selected} == {
        dataset[iid].repo for iid in dataset
    }

    # 选取数量少于仓库数时，前几个也应来自不同仓库。
    few = select_stratified_instances(dataset, 3, seed=0)
    assert len({dataset[iid].repo for iid in few}) == 3


def test_stratified_selection_truncates_to_dataset_size() -> None:
    """count 超过数据集规模时应截断为全部实例，count<=0 返回空列表。"""

    dataset = _fake_swebench_dataset()
    assert len(select_stratified_instances(dataset, 1000)) == len(dataset)
    assert select_stratified_instances(dataset, 0) == []
    assert select_stratified_instances(dataset, -1) == []


def test_real_world_repeats_produce_indexed_rows_and_summary(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """repeats=2 应产出 repeat_index 1..2 的行，报告中含 repeats/summary。"""

    from research.evals import real_world

    dataset = _fake_swebench_dataset()
    instance_ids = select_stratified_instances(dataset, 2, seed=0)
    monkeypatch.setattr(real_world, "REAL_EVAL_ROOT", tmp_path)
    monkeypatch.setattr(real_world, "load_swebench_lite_dev", lambda: dataset)

    def fake_audit(instance: SweBenchInstance, run_id: str) -> dict[str, object]:
        return {
            "instance_id": instance.instance_id,
            "baseline_failed": True,
            "unstable_pass_to_pass": [],
        }

    def fake_evaluate(
        instance: SweBenchInstance,
        variant: str,
        run_id: str,
        excluded_targets: tuple[str, ...] | list[str] = (),
        repeat_index: int = 1,
    ) -> dict[str, object]:
        suffix = _repeat_suffix(variant, repeat_index)
        return {
            "instance_id": instance.instance_id,
            "repo": instance.repo,
            "variant": variant,
            "repeat_index": repeat_index,
            "success": True,
            "tests_passed": True,
            "elapsed_seconds": float(repeat_index),
            "total_tokens": repeat_index * 100,
            "patch_path": f"{suffix}.patch",
            "trace_path": f"{suffix}.jsonl",
        }

    monkeypatch.setattr(real_world, "audit_real_instance", fake_audit)
    monkeypatch.setattr(real_world, "evaluate_real_instance", fake_evaluate)

    report_path = real_world.run_real_world_evaluation(
        instance_ids=tuple(instance_ids),
        variants=("single_no_rag",),
        repeats=2,
    )

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["repeats"] == 2
    assert {row["repeat_index"] for row in payload["rows"]} == {1, 2}
    assert len(payload["rows"]) == 2 * len(instance_ids)

    # 第 2 轮证据文件命名带 __r2，第 1 轮保持原命名。
    for row in payload["rows"]:
        if row["repeat_index"] == 2:
            assert "__r2" in row["patch_path"]
        else:
            assert "__r2" not in row["patch_path"]

    summary = payload["summary"]
    assert len(summary) == len(instance_ids)
    for entry in summary.values():
        assert entry["runs"] == 2
        assert set(entry["success_rate"]) == {"mean", "std"}
        assert set(entry["total_tokens"]) == {"mean", "std"}
        assert set(entry["elapsed_seconds"]) == {"mean", "std"}


def test_real_world_rejects_invalid_repeats_and_variant() -> None:
    """repeats 必须 >=1，未知 variant 应立即报错。"""

    from research.evals import real_world

    with pytest.raises(ValueError, match="repeats"):
        real_world.run_real_world_evaluation(
            instance_ids=("owner__repo-1",),
            variants=("single_no_rag",),
            repeats=0,
        )
    with pytest.raises(ValueError, match="未知 variant"):
        real_world.run_real_world_evaluation(
            instance_ids=("owner__repo-1",),
            variants=("bogus_variant",),
        )


def test_real_git_stdin_preserves_utf8_and_lf(tmp_path):
    """! @brief 用真实 Git 校验 WorkBuddy 字节 stdin 改动，不依赖 Docker/模型。"""
    import hashlib
    from research.evals.real_world import _run_git
    content = "补丁第一行\nsecond line\n"
    payload = content.encode("utf-8")
    expected = hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()
    assert _run_git(["hash-object", "--stdin"], cwd=tmp_path, input_text=content).strip() == expected
