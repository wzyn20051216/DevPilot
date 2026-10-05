"""! @brief 动态 Agent 策略路由的纯单元测试。"""

from pathlib import Path

import hashlib

import pytest
from pytest import MonkeyPatch

from backend.src.agents import base_tool_agent
from backend.src.agents.single_developer_agent import (
    SINGLE_DEVELOPER_CORE_PROMPT,
    SINGLE_DEVELOPER_PROMPT,
    SINGLE_DEVELOPER_PROBE_PROMPT,
    STRATEGY_OUTLINE_ADDENDUM,
    STRATEGY_PROBE_ADDENDUM,
    STRATEGY_GUARD_ADDENDUM,
    SingleDeveloperAgent,
)
from backend.src.agents.strategy import AgentStrategy, decide_strategy
from backend.src.config import Settings, settings
from backend.src.models.agent_state import PlanStep
from backend.src.models.task import DevelopmentTask
from backend.src.services.task_policy import decide_task_strategy


BASE_TOOLS = {
    "list_files",
    "read_file",
    "search_code",
    "write_file",
    "replace_in_file",
    "git_diff",
    "run_test",
    "run_command",
    "protocol_probe",
}


def _settings(**overrides: object) -> Settings:
    """! @brief 构造不读取本地 .env 的路由测试配置。"""

    values: dict[str, object] = {
        "strategy_outline_min_lines": 5,
        "strategy_outline_min_repo_files": 4,
        "strategy_python_min_ratio": 0.5,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _write_lines(path: Path, count: int) -> None:
    """! @brief 写入指定行数的最小 Python 文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("value = 1\n" * count, encoding="utf-8")


def test_explicit_small_python_file_disables_outline(tmp_path: Path) -> None:
    """! @brief 明确小文件应直接局部读取，不做结构导航。"""

    _write_lines(tmp_path / "pkg" / "small.py", 4)
    decision = decide_strategy(tmp_path, "修改 pkg/small.py 的文档", _settings())
    assert decision.use_outline is False
    assert decision.mode == "standard"
    assert decision.metrics["target_line_counts"] == {"pkg/small.py": 4}


def test_explicit_large_python_file_enables_outline(tmp_path: Path) -> None:
    """! @brief 明确大文件达到边界值时应启用 code_outline。"""

    _write_lines(tmp_path / "pkg" / "large.py", 5)
    decision = decide_strategy(tmp_path, "修改 pkg/large.py 中的逻辑", _settings())
    assert decision.use_outline is True
    assert decision.mode == "outline_only"


def test_repo_size_controls_outline_without_file_reference(tmp_path: Path) -> None:
    """! @brief 无明确目标时，仓库文件数在阈值上开启结构导航。"""

    for index in range(4):
        _write_lines(tmp_path / f"module_{index}.py", 1)
    decision = decide_strategy(tmp_path, "修复系统中的一个问题", _settings())
    assert decision.use_outline is True
    assert decision.metrics["source_files"] == 4


def test_non_python_dominant_repo_disables_outline(tmp_path: Path) -> None:
    """! @brief code_outline 不应用于非 Python 主导仓库。"""

    _write_lines(tmp_path / "only.py", 10)
    for index in range(3):
        (tmp_path / f"module_{index}.ts").write_text("export {};\n", encoding="utf-8")
    decision = decide_strategy(tmp_path, "修改 only.py 中的逻辑", _settings())
    assert decision.use_outline is False
    assert decision.metrics["python_ratio"] == 0.25


@pytest.mark.parametrize(
    "question",
    [
        "修复 AttributeError",
        "修复 TypeError",
        "迭代器的 __next__ 协议不正确",
        "序列化生成器时失败",
        "调用 next(obj) 的结果不正确",
    ],
)
def test_behavior_semantics_enforce_probe(tmp_path: Path, question: str) -> None:
    """! @brief 行为/协议语义问题必须先做运行时探针。"""

    _write_lines(tmp_path / "module.py", 1)
    decision = decide_strategy(tmp_path, question, _settings())
    assert decision.enforce_probe is True
    assert decision.mode == "probe_only"


def test_behavior_keyword_overrides_documentation_exemption(tmp_path: Path) -> None:
    """! @brief 文档引用不能使协议缺陷绕过前后复现门禁。"""

    _write_lines(tmp_path / "module.py", 1)
    decision = decide_strategy(
        tmp_path, "修复 documentation 示例中的 TypeError", _settings()
    )
    assert decision.enforce_probe is True
    assert decision.require_reproducer is True
    assert decision.require_post_edit_probe is True


def test_standard_bug_receives_reproducer_instructions(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    """! @brief 普通代码缺陷也必须获得与机器门禁一致的复现说明。"""

    _write_lines(tmp_path / "module.py", 1)
    monkeypatch.setattr(base_tool_agent, "create_client", lambda: object())
    strategy = decide_strategy(tmp_path, "修复计算结果错误", _settings())
    agent = SingleDeveloperAgent(str(tmp_path), False, strategy=strategy)
    assert strategy.enforce_probe is False
    assert STRATEGY_PROBE_ADDENDUM in agent.system_prompt
    assert agent.workflow_contract.auto_replay_reproducer is True


@pytest.mark.parametrize(
    "question",
    ["更新 README 文档", "修复字符串格式化", "调整 configuration 配置"],
)
def test_non_behavior_tasks_do_not_enforce_probe(tmp_path: Path, question: str) -> None:
    """! @brief 文档、格式和配置任务不强制运行时探针。"""

    _write_lines(tmp_path / "module.py", 1)
    assert decide_strategy(tmp_path, question, _settings()).enforce_probe is False


def test_strategy_is_deterministic(tmp_path: Path) -> None:
    """! @brief 同一输入多次调用必须产生相同决策。"""

    _write_lines(tmp_path / "module.py", 6)
    first = decide_strategy(tmp_path, "修复 module.py 的 TypeError", _settings())
    second = decide_strategy(tmp_path, "修复 module.py 的 TypeError", _settings())
    assert first == second
    assert first.mode == "outline_and_probe"
    assert first.reasons


def test_static_fallback_when_router_disabled(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """! @brief 关闭路由器时应回退为静态、可解释的决策。"""

    monkeypatch.setattr(settings, "strategy_router_enabled", False)
    task = DevelopmentTask(
        id="strategy-off",
        repo_path=str(tmp_path),
        question="修复 TypeError",
        status="running",
        execution_mode="single_no_rag",
        plan=[PlanStep(id=1, title="t", description="d")],
    )
    decision = decide_task_strategy(task)
    assert decision.mode == "static_fallback"
    assert decision.use_outline is False
    assert decision.enforce_probe is False
    assert decision.reasons

    # API 与 Worker 都必须把 static_fallback 转回 strategy=None，
    # 否则动态分段组装会改变历史基线提示词。
    from backend.src.main import _create_task_runner
    from backend.src.worker import _worker_runner_factory

    monkeypatch.setattr(base_tool_agent, "create_client", lambda: object())
    inline = _create_task_runner(task, lambda: False)
    worker = _worker_runner_factory(task, lambda: False)
    assert inline.agent.system_prompt == SINGLE_DEVELOPER_PROMPT
    assert worker.agent.system_prompt == SINGLE_DEVELOPER_PROMPT
    assert inline.agent.allowed_tools == worker.agent.allowed_tools == BASE_TOOLS


def test_strategy_none_preserves_baseline_snapshot(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """! @brief strategy=None 的提示词和工具集必须与历史基线逐字一致。"""

    monkeypatch.setattr(base_tool_agent, "create_client", lambda: object())
    agent = SingleDeveloperAgent(str(tmp_path), enable_rag=False, strategy=None)
    assert agent.system_prompt == SINGLE_DEVELOPER_PROMPT
    assert hashlib.sha256(agent.system_prompt.encode()).hexdigest() == (
        "8031c5959076423d5e8ea4618046e6a33531770d616b26e7db07e86f228a1199"
    )
    assert agent.allowed_tools == BASE_TOOLS
    assert agent.max_iterations == 14


def test_agent_assembles_adaptive_strategy(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """! @brief 动态决策应独立控制工具、提示词段和轮次上限。"""

    monkeypatch.setattr(base_tool_agent, "create_client", lambda: object())
    strategy = AgentStrategy(
        use_outline=True,
        enforce_probe=True,
        max_iterations=9,
        mode="outline_and_probe",
        reasons=["测试"],
        metrics={},
    )
    agent = SingleDeveloperAgent(
        str(tmp_path),
        enable_rag=False,
        max_iterations=99,
        strategy=strategy,
    )
    assert agent.allowed_tools == BASE_TOOLS | {"code_outline", "record_candidates"}
    assert agent.system_prompt == (
        SINGLE_DEVELOPER_CORE_PROMPT.replace(
            "你最多有 14 轮工具交互。", "你最多有 9 轮工具交互。"
        )
        + STRATEGY_GUARD_ADDENDUM
        + STRATEGY_OUTLINE_ADDENDUM
        + SINGLE_DEVELOPER_PROBE_PROMPT
        + STRATEGY_PROBE_ADDENDUM
    )
    assert agent.max_iterations == 9
    assert agent.workflow_contract is not None
    assert agent.workflow_contract.candidate_count == 2
    assert agent.workflow_contract.require_passing_test_after_edit is True


def test_adaptive_real_world_row_records_strategy(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """! @brief single_adaptive 的行记录必须保留完整决策供归因。"""

    from research.evals import real_world

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_lines(workspace / "module.py", 1)
    instance = real_world.SweBenchInstance(
        instance_id="owner__repo-1",
        repo="owner/repo",
        base_commit="abc",
        problem_statement="修复 TypeError",
        gold_patch="",
        test_patch="",
        fail_to_pass=("tests/test_module.py::test_bug",),
        pass_to_pass=(),
    )

    monkeypatch.setattr(real_world, "REAL_EVAL_ROOT", tmp_path / "results")
    monkeypatch.setattr(
        real_world,
        "create_real_workspace",
        lambda _instance, _run_id, _label: workspace,
    )
    captured: dict[str, AgentStrategy | None] = {}

    def fake_run_agent(
        _instance: object,
        _variant: str,
        _workspace: Path,
        strategy: AgentStrategy | None = None,
    ) -> list[object]:
        captured["strategy"] = strategy
        return []

    monkeypatch.setattr(real_world, "_run_agent", fake_run_agent)
    monkeypatch.setattr(real_world, "_candidate_paths", lambda _workspace: [])
    monkeypatch.setattr(
        real_world,
        "verify_real_patch",
        lambda *_args, **_kwargs: {
            "passed": False,
            "targets": ["tests/test_module.py"],
            "ignored_environment_failures": [],
            "unexpected_failures": [],
            "results": [],
        },
    )

    row = real_world.evaluate_real_instance(
        instance,
        "single_adaptive",
        "run-id",
    )
    assert captured["strategy"] is not None
    assert row["strategy"]["mode"] == "probe_only"
    assert row["strategy"]["reasons"]
    assert row["strategy"]["metrics"]["behavior_hits"] == ["typeerror"]
    assert row["strategy"]["require_reproducer"] is True
    assert row["strategy"]["require_post_edit_probe"] is True
    assert row["workflow_compliance"] is None


def test_real_workspace_enables_longpaths_before_checkout(
    tmp_path: Path, monkeypatch: MonkeyPatch,
) -> None:
    """! @brief Windows 深层裁判目录应在写出源码前启用长路径支持。"""

    from research.evals import real_world

    instance = real_world.SweBenchInstance(
        instance_id="owner__repo-1", repo="owner/repo", base_commit="abc",
        problem_statement="bug", gold_patch="", test_patch="",
        fail_to_pass=(), pass_to_pass=(),
    )
    monkeypatch.setattr(real_world, "REAL_EVAL_ROOT", tmp_path / "results")
    monkeypatch.setattr(real_world, "_cached_repository", lambda _: tmp_path / "cache")
    monkeypatch.setattr(real_world, "_overlay_build_artifacts", lambda *_: 0)
    calls: list[list[str]] = []

    def run_git(arguments: list[str], **_: object) -> str:
        calls.append(arguments)
        return ""

    monkeypatch.setattr(real_world, "_run_git", run_git)
    real_world.create_real_workspace(instance, "run", "adaptive-verifier")
    enabled = calls.index(["config", "core.longpaths", "true"])
    assert enabled < calls.index(["checkout", "--detach", "abc"])
    assert enabled < calls.index(["reset", "--hard"])
