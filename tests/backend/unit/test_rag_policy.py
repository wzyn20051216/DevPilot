"""动态 RAG 策略与接线回归测试。"""

from pathlib import Path

from pytest import MonkeyPatch

from backend.src.agents import base_tool_agent
from backend.src.config import settings
from backend.src.main import _create_task_runner
from backend.src.models.agent_state import PlanStep
from backend.src.models.task import DevelopmentTask
from backend.src.rag.policy import (
    count_source_files,
    decide_rag,
    extract_query_signals,
)


def test_count_source_files_counts_code_and_ignores_noise(
    tmp_path: Path,
) -> None:
    """源码文件应被统计，噪声目录与文档/配置应被跳过。"""

    (tmp_path / "a.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "b.ts").write_text("const x = 1;\n", encoding="utf-8")
    (tmp_path / "readme.md").write_text("# readme\n", encoding="utf-8")
    (tmp_path / "data.txt").write_text("plain\n", encoding="utf-8")

    noise = tmp_path / "node_modules"
    noise.mkdir()
    (noise / "c.js").write_text("module.exports = 1;\n", encoding="utf-8")

    assert count_source_files(tmp_path) == 2


def test_count_source_files_missing_path_returns_zero(
    tmp_path: Path,
) -> None:
    """仓库路径不存在时按 0 处理，而不是抛异常。"""

    assert count_source_files(tmp_path / "does-not-exist") == 0


def test_extract_query_signals_file_reference() -> None:
    """文件名引用应作为最高置信度信号被提取。"""

    signals = extract_query_signals(
        "请修改 src/utils/helpers.py 中的 fetchData 函数"
    )
    assert "src/utils/helpers.py" in signals


def test_extract_query_signals_symbol() -> None:
    """CamelCase 符号名应被提取为定位信号。"""

    signals = extract_query_signals(
        "修复 UserProfileService 的空指针问题"
    )
    assert "UserProfileService" in signals


def test_extract_query_signals_error_type() -> None:
    """报错类型名应从故障描述中被提取。"""

    signals = extract_query_signals(
        "运行时报 AttributeError: 'NoneType' object has no attribute 'value'"
    )
    assert "AttributeError" in signals


def test_extract_query_signals_quoted_snippet() -> None:
    """引号内代码片段应去掉引号后作为信号。"""

    signals = extract_query_signals(
        "把 `def handler():` 改成异步版本"
    )
    assert "def handler():" in signals


def test_extract_query_signals_no_signal() -> None:
    """纯自然语言且无定位线索的问题应返回空信号。"""

    assert extract_query_signals("请帮我优化一下这段代码的性能") == []


def test_decide_rag_covers_four_modes(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """小/大仓库与中间地带歧义/明确四种分支都要被覆盖。"""

    monkeypatch.setattr(settings, "rag_auto_small_repo_files", 3)
    monkeypatch.setattr(settings, "rag_auto_large_repo_files", 6)
    monkeypatch.setattr(settings, "rag_auto_query_signal_chars", 10)

    small = tmp_path / "small"
    small.mkdir()
    for index in range(2):
        (small / f"s{index}.py").write_text("pass\n", encoding="utf-8")

    large = tmp_path / "large"
    large.mkdir()
    for index in range(6):
        (large / f"l{index}.py").write_text("pass\n", encoding="utf-8")

    mid = tmp_path / "mid"
    mid.mkdir()
    for index in range(4):
        (mid / f"m{index}.py").write_text("pass\n", encoding="utf-8")

    small_decision = decide_rag(small, "任意问题描述")
    assert small_decision.enabled is False
    assert small_decision.mode == "small_repo"

    large_decision = decide_rag(large, "任意问题描述")
    assert large_decision.enabled is True
    assert large_decision.mode == "large_repo"

    ambiguous = decide_rag(mid, "fix bug")
    assert ambiguous.enabled is True
    assert ambiguous.mode == "ambiguous_query"

    specific = decide_rag(mid, "修改 helpers.py 的 add 函数")
    assert specific.enabled is False
    assert specific.mode == "specific_query"


def test_decide_rag_is_deterministic(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """同一输入两次调用必须得到完全一致的决策。"""

    repo = tmp_path / "repo"
    repo.mkdir()
    for index in range(4):
        (repo / f"f{index}.py").write_text("pass\n", encoding="utf-8")

    monkeypatch.setattr(settings, "rag_auto_small_repo_files", 3)
    monkeypatch.setattr(settings, "rag_auto_large_repo_files", 6)
    monkeypatch.setattr(settings, "rag_auto_query_signal_chars", 10)

    first = decide_rag(repo, "fix bug")
    second = decide_rag(repo, "fix bug")

    assert first == second
    assert first.metrics["source_files"] == 4


def _make_task(
    repo_path: str,
    execution_mode: str,
    question: str = "fix bug",
) -> DevelopmentTask:
    """构造一个最小合法、不触发数据库的开发任务对象。"""

    return DevelopmentTask(
        id="task-test",
        repo_path=repo_path,
        question=question,
        status="running",
        execution_mode=execution_mode,
        plan=[PlanStep(id=1, title="t", description="d")],
    )


def test_create_task_runner_dynamic_rag_auto(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """auto 模式应按仓库规模覆盖 execution_mode 的显式 RAG 语义。"""

    # 不真正创建 OpenAI 客户端，只验证 allowed_tools 白名单。
    monkeypatch.setattr(base_tool_agent, "create_client", lambda: object())
    monkeypatch.setattr(settings, "rag_mode", "auto")
    monkeypatch.setattr(settings, "rag_auto_small_repo_files", 3)
    monkeypatch.setattr(settings, "rag_auto_large_repo_files", 6)

    large = tmp_path / "large"
    large.mkdir()
    for index in range(6):
        (large / f"f{index}.py").write_text("pass\n", encoding="utf-8")

    small = tmp_path / "small"
    small.mkdir()
    (small / "a.py").write_text("pass\n", encoding="utf-8")

    large_runner = _create_task_runner(
        _make_task(str(large), "single_no_rag"),
        lambda: False,
    )
    assert "retrieve_code" in large_runner.agent.allowed_tools

    small_runner = _create_task_runner(
        _make_task(str(small), "single_rag"),
        lambda: False,
    )
    assert "retrieve_code" not in small_runner.agent.allowed_tools


def test_create_task_runner_manual_fallback(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """manual 模式必须回退到 execution_mode 的显式语义。"""

    monkeypatch.setattr(base_tool_agent, "create_client", lambda: object())
    monkeypatch.setattr(settings, "rag_mode", "manual")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("pass\n", encoding="utf-8")

    rag_runner = _create_task_runner(
        _make_task(str(repo), "single_rag"),
        lambda: False,
    )
    assert "retrieve_code" in rag_runner.agent.allowed_tools

    no_rag_runner = _create_task_runner(
        _make_task(str(repo), "single_no_rag"),
        lambda: False,
    )
    assert "retrieve_code" not in no_rag_runner.agent.allowed_tools


def test_worker_auto_policy_matches_inline(tmp_path, monkeypatch):
    """! @brief 独立 Worker 和 API 对小仓库自动关闭 RAG 的决策一致。"""
    from backend.src.worker import _worker_runner_factory
    monkeypatch.setattr(base_tool_agent, "create_client", lambda: object())
    monkeypatch.setattr(settings, "rag_mode", "auto")
    (tmp_path / "a.py").write_text("pass", encoding="utf-8")
    task = _make_task(str(tmp_path), "single_rag")
    inline = _create_task_runner(task, lambda: False)
    worker = _worker_runner_factory(task, lambda: False)
    assert "retrieve_code" not in worker.agent.allowed_tools
    assert worker.agent.allowed_tools == inline.agent.allowed_tools
