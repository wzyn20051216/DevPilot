"""第十四至十六关 Evals 基础设施测试。

这些测试不调用 LLM，专门覆盖数据加载、Workspace 隔离、事件计数和 SQLite
序列化，保证昂贵的四组实验开始前基础设施已经可靠。
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from backend.src.database import connection
from backend.src.database.evaluation_repository import evaluation_repository
from backend.src.evals import exporter, plotter
from backend.src.evals.analysis import (
    aggregate_case_results,
    bootstrap_mean_ci,
    build_ablation_report,
    get_paired_deltas,
    summarize_by_difficulty,
    wilcoxon_paired_test,
)
from backend.src.evals.audit import build_audit_report
from backend.src.evals.dataset import (
    calculate_dataset_fingerprint,
    load_benchmark_cases,
    load_case,
)
from backend.src.evals.metrics import summarize_results
from backend.src.evals.models import BenchmarkCase, EvaluationResult, EvaluationVariant
from backend.src.evals.runner import (
    VARIANTS,
    collect_event_metrics,
    collect_usage,
    create_workspace,
)
from backend.src.main import app
from backend.src.models.agent_state import AgentEvent


def test_load_add_bug_case() -> None:
    """add_bug 用例及其 fixture 应能被正常加载。"""

    case = load_case("add_bug")
    assert case.repo_fixture == "add_bug_repo"
    assert case.verification_target == "tests/test_calculator.py"
    assert case.difficulty == "easy"
    assert case.category == "bugfix"
    assert "single_file" in case.tags


def test_dataset_covers_all_difficulty_levels() -> None:
    """正式实验数据集应在三档难度上保持均衡。"""

    cases = load_benchmark_cases()
    assert {case.difficulty for case in cases} == {"easy", "medium", "hard"}
    assert len(cases) == 9
    assert all(sum(item.difficulty == level for item in cases) == 3 for level in ("easy", "medium", "hard"))
    assert all(case.tags for case in cases)
    assert len(calculate_dataset_fingerprint()) == 64


def test_all_benchmark_baselines_fail_before_agent_changes() -> None:
    """每个错误版 fixture 的 verifier 都必须先失败，防止无效样本混入实验。"""

    report = build_audit_report(execute=True)
    assert report["valid"] is True
    assert report["case_count"] == 9
    assert all(row["baseline_failed"] is True for row in report["cases"])


def test_variants_use_independent_git_workspaces(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """不同 variant 必须得到互不影响且保留 .git 的仓库副本。"""

    from backend.src.evals import runner

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

    from backend.src.evals import runner

    monkeypatch.setattr(runner, "EVAL_WORKSPACE_ROOT", tmp_path)
    case = load_case("add_bug")
    first = create_workspace(case, "single_no_rag", "test-run", repeat_index=1)
    second = create_workspace(case, "single_no_rag", "test-run", repeat_index=2)

    assert first != second
    assert "repeat-1" in first.parts
    assert "repeat-2" in second.parts
    assert (first / ".git").is_dir()
    assert (second / ".git").is_dir()


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
    ]

    assert collect_event_metrics(events) == (1, 2, 1)
    assert collect_usage(events) == (30, 5, 35)


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

    from backend.src.evals import runner

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

    run_id = runner.run_full_experiment(repeats=2)
    assert len(calls) == 9 * 4 * 2
    assert {call[2] for call in calls} == {run_id}
    assert {call[3] for call in calls} == {1, 2}
    assert all(call[4] is True for call in calls)

    config_path = tmp_path / "experiments" / run_id / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["repeats"] == 2
    assert config["benchmark_cases"] == 9
    assert len(config["dataset_sha256"]) == 64
    assert config["random_seed"] == 42
    assert config["variants"] == list(VARIANTS)


def test_full_experiment_resume_skips_persisted_combinations(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """断点续跑不得重复执行已经写入 SQLite 的付费组合。"""

    from backend.src.evals import runner

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
    assert len(calls) == 9 * 4

    calls.clear()
    resumed_run_id = runner.run_full_experiment(repeats=1, run_id=run_id)
    assert resumed_run_id == run_id
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
        params={"run_id": "dashboard-run"},
    )
    assert response.status_code == 200
    assert response.json()["single_no_rag"]["runs"] == 1

    difficulty_response = TestClient(app).get(
        "/api/evals/difficulty",
        params={"run_id": "analysis-run"},
    )
    assert difficulty_response.status_code == 200
    assert len(difficulty_response.json()) == 4

    ablation_response = TestClient(app).get(
        "/api/evals/ablation",
        params={"run_id": "analysis-run"},
    )
    assert ablation_response.status_code == 200
    assert len(ablation_response.json()) == 20
