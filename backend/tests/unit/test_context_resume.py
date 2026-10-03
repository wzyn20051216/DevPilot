"""上下文断点恢复的离线测试。

覆盖 context_store 存储层 roundtrip，以及 TaskExecutionService 在单 Agent
模式下的检查点注入、恢复事件产出、开关与异常容错。全部离线，不依赖 LLM。
"""

import time
from collections.abc import Callable, Iterator
from pathlib import Path

from pytest import MonkeyPatch

from backend.src.config import settings
from backend.src.database import connection
from backend.src.database.task_repository import TaskRepository
from backend.src.models.agent_state import AgentEvent, PlanStep
from backend.src.services import context_store
from backend.src.services.task_execution_service import TaskExecutionService


def _create_task(repository: TaskRepository):
    return repository.create_task(
        repo_path="repo",
        question="fix",
        plan=[PlanStep(id=1, title="fix", description="fix")],
    )


def _wait_for_status(
    repository: TaskRepository,
    task_id: str,
    expected: str,
    timeout: float = 15.0,
) -> None:
    """等待后台线程把任务推进到目标状态。

    超时给到 15 秒：全量套件运行时机器负载高（Docker 测试并行收尾），
    2 秒的默认值曾在负载下把正常完成的任务误判为失败（偶发 flake），
    这里宁可多等也不能让时序抖动污染测试结果。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if repository.get_task(task_id).status == expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"任务未进入 {expected}")


def _context_row(task_id: str, agent_name: str):
    """! @brief 直接读取检查点行，用于断言 count/truncated 等非消息字段。"""

    with connection.get_connection() as conn:
        return conn.execute(
            """
            SELECT message_count, truncated
            FROM agent_contexts
            WHERE task_id = ? AND agent_name = ?
            """,
            (task_id, agent_name),
        ).fetchone()


class _FakeAgent:
    """! @brief 模拟单 Agent，暴露检查点钩子所需的最小接口。"""

    def __init__(self) -> None:
        self.name = "coder"
        self.checkpoint_callback: Callable[[str, list[dict]], None] | None = None
        self.initial_messages: list[dict] | None = None


class _FakeRunner:
    """! @brief 模拟 SingleAgentTaskRunner：暴露 .agent 并在流开始时写检查点。"""

    def __init__(self, repo_path: str, cancel_check: Callable[[], bool]) -> None:
        self.repo_path = repo_path
        self.cancel_check = cancel_check
        self.agent = _FakeAgent()

    def execute_stream(self, question: str, plan: list) -> Iterator[AgentEvent]:
        # 模拟真实 BaseToolAgent 的检查点行为：进入循环前写一次检查点。
        # 若钩子未被注入（开关关闭），跳过写入避免 None 调用。
        if self.agent.checkpoint_callback is not None:
            self.agent.checkpoint_callback(
                self.agent.name,
                [{"role": "user", "content": "m1"}],
            )
        yield AgentEvent(type="start", agent="coder", message="start")
        yield AgentEvent(type="final", agent="orchestrator", message="done")


# ---------------------------------------------------------------------------
# 1. context_store 存储层
# ---------------------------------------------------------------------------


def test_context_store_roundtrip(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """save → load 应往返一致；无记录时 load 返回 None。"""

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "ctx.db")
    connection.init_database()
    task = _create_task(TaskRepository())

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "问"},
        {"role": "assistant", "content": "答"},
    ]
    context_store.save_context(task.id, "coder", messages)

    assert context_store.load_context(task.id, "coder") == messages
    assert context_store.load_context(task.id, "planner") is None
    assert context_store.load_context("missing", "coder") is None


def test_context_store_upsert_latest(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """同一 (task_id, agent) 重复保存应 UPSERT，只保留最新一份。"""

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "ctx.db")
    connection.init_database()
    task = _create_task(TaskRepository())

    context_store.save_context(task.id, "coder", [{"role": "user", "content": "v1"}])
    context_store.save_context(task.id, "coder", [{"role": "user", "content": "v2"}])

    assert context_store.load_context(task.id, "coder") == [
        {"role": "user", "content": "v2"}
    ]
    # UPSERT 不应产生第二行。
    with connection.get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM agent_contexts WHERE task_id = ? AND agent_name = 'coder'",
            (task.id,),
        ).fetchone()[0]
    assert count == 1


def test_context_store_max_messages_truncates(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """超出 max_messages 时只保留最近消息，并置 truncated=1。"""

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "ctx.db")
    connection.init_database()
    task = _create_task(TaskRepository())

    messages = [{"role": "user", "content": f"m{i}"} for i in range(5)]
    context_store.save_context(task.id, "coder", messages, max_messages=3)

    assert context_store.load_context(task.id, "coder") == messages[-3:]

    row = _context_row(task.id, "coder")
    assert row is not None
    assert row["message_count"] == 3
    assert row["truncated"] == 1


def test_context_store_clear(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """clear_context 支持指定 Agent 与清空整个任务，且不影响其它任务。"""

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "ctx.db")
    connection.init_database()
    repository = TaskRepository()
    task1 = _create_task(repository)
    task2 = _create_task(repository)

    context_store.save_context(task1.id, "coder", [{"role": "user", "content": "x"}])
    context_store.save_context(task1.id, "tester", [{"role": "user", "content": "y"}])
    context_store.save_context(task2.id, "coder", [{"role": "user", "content": "z"}])

    assert context_store.clear_context(task1.id, "coder") == 1
    assert context_store.load_context(task1.id, "coder") is None
    assert context_store.load_context(task1.id, "tester") is not None

    assert context_store.clear_context(task1.id) == 1
    assert context_store.load_context(task1.id, "tester") is None
    assert context_store.load_context(task2.id, "coder") is not None

    assert context_store.clear_context(task2.id) == 1
    assert context_store.clear_context("nope") == 0


# ---------------------------------------------------------------------------
# 2. 端到端恢复
# ---------------------------------------------------------------------------


def test_context_resume_end_to_end(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """第一次全新执行保存检查点；第二次执行注入该检查点并产出恢复事件。"""

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "ctx.db")
    connection.init_database()
    repository = TaskRepository()
    task = _create_task(repository)

    # 第一次执行：无检查点，initial_messages 应为 None。
    first_runner = _FakeRunner(repo_path="repo", cancel_check=lambda: False)
    service = TaskExecutionService(repository, lambda t, c: first_runner)
    assert repository.claim_status(task.id, {"awaiting_approval"}, "running")
    service.start(task.id)
    _wait_for_status(repository, task.id, "completed")

    assert first_runner.agent.initial_messages is None
    assert context_store.load_context(task.id, "coder") == [
        {"role": "user", "content": "m1"}
    ]

    # 模拟服务重启后的 resume：迁移回 interrupted 再领取为 running。
    repository.set_status(task.id, "interrupted")
    assert repository.claim_status(task.id, {"interrupted"}, "running")

    second_runner = _FakeRunner(repo_path="repo", cancel_check=lambda: False)
    service2 = TaskExecutionService(repository, lambda t, c: second_runner)
    service2.start(task.id)
    _wait_for_status(repository, task.id, "completed")

    assert second_runner.agent.initial_messages == [
        {"role": "user", "content": "m1"}
    ]

    # 事件流中应出现一条"恢复"事件，位于第二次执行的 start 之前。
    events = repository.get_events(task.id)
    resume_events = [e for e in events if "检查点" in e["message"]]
    assert len(resume_events) == 1
    assert resume_events[0]["type"] == "thinking"
    assert resume_events[0]["agent"] == "orchestrator"
    assert resume_events[0]["data"]["restored_messages"] == 1


def test_checkpoint_disabled_skips_injection(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """agent_checkpoint_enabled=False 时不注入、不保存检查点。"""

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "ctx.db")
    connection.init_database()
    monkeypatch.setattr(settings, "agent_checkpoint_enabled", False)

    repository = TaskRepository()
    task = _create_task(repository)

    runner = _FakeRunner(repo_path="repo", cancel_check=lambda: False)
    service = TaskExecutionService(repository, lambda t, c: runner)
    assert repository.claim_status(task.id, {"awaiting_approval"}, "running")
    service.start(task.id)
    _wait_for_status(repository, task.id, "completed")

    # 开关关闭时不应注入初始消息与写回调。
    assert runner.agent.initial_messages is None
    assert runner.agent.checkpoint_callback is None
    assert context_store.load_context(task.id, "coder") is None


def test_checkpoint_write_failure_does_not_block(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """检查点写入抛异常不应影响任务完成。"""

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "ctx.db")
    connection.init_database()

    repository = TaskRepository()
    task = _create_task(repository)

    runner = _FakeRunner(repo_path="repo", cancel_check=lambda: False)
    service = TaskExecutionService(repository, lambda t, c: runner)
    assert repository.claim_status(task.id, {"awaiting_approval"}, "running")

    def boom(*args, **kwargs) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(context_store, "save_context", boom)
    service.start(task.id)
    _wait_for_status(repository, task.id, "completed")

    assert repository.get_task(task.id).status == "completed"


# ---------------------------------------------------------------------------
# 3. 多 Agent 上下文恢复（技术手册 10.1 补全）
# ---------------------------------------------------------------------------


class _FakeOrchestrator:
    """! @brief 模拟多 Agent 编排器：暴露 planner/coder/tester/reviewer 四角色。"""

    def __init__(self, repo_path: str, cancel_check: Callable[[], bool]) -> None:
        self.repo_path = repo_path
        self.cancel_check = cancel_check
        self.planner = _FakeAgent()
        self.planner.name = "planner"
        self.coder = _FakeAgent()
        self.coder.name = "coder"
        self.tester = _FakeAgent()
        self.tester.name = "tester"
        self.reviewer = _FakeAgent()
        self.reviewer.name = "reviewer"

    def execute_stream(self, question: str, plan: list) -> Iterator[AgentEvent]:
        # 每个角色 agent 各自写一次检查点，模拟真实 BaseToolAgent 行为。
        for agent in (self.planner, self.coder, self.tester, self.reviewer):
            if agent.checkpoint_callback is not None:
                agent.checkpoint_callback(
                    agent.name,
                    [{"role": "user", "content": f"{agent.name}-m1"}],
                )
        yield AgentEvent(type="start", agent="orchestrator", message="start")
        yield AgentEvent(type="final", agent="orchestrator", message="done")


def test_multi_agent_checkpoint_injection_and_restore(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """多 Agent 模式：四角色各自写检查点，恢复时按角色名分别注入。"""

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "ctx.db")
    connection.init_database()
    repository = TaskRepository()
    task = _create_task(repository)

    # 第一次执行：四角色各自落盘一份检查点。
    first = _FakeOrchestrator(repo_path="repo", cancel_check=lambda: False)
    service = TaskExecutionService(repository, lambda t, c: first)
    assert repository.claim_status(task.id, {"awaiting_approval"}, "running")
    service.start(task.id)
    _wait_for_status(repository, task.id, "completed")

    for role in ("planner", "coder", "tester", "reviewer"):
        assert context_store.load_context(task.id, role) == [
            {"role": "user", "content": f"{role}-m1"}
        ]

    # 模拟重启：仅 coder 有历史上下文（其它角色第一次执行），验证按角色
    # 精准注入而非一刀切。
    repository.set_status(task.id, "interrupted")
    assert repository.claim_status(task.id, {"interrupted"}, "running")

    second = _FakeOrchestrator(repo_path="repo", cancel_check=lambda: False)
    service2 = TaskExecutionService(repository, lambda t, c: second)
    service2.start(task.id)
    _wait_for_status(repository, task.id, "completed")

    # 四个角色都应被注入各自的 initial_messages。
    assert second.planner.initial_messages == [{"role": "user", "content": "planner-m1"}]
    assert second.coder.initial_messages == [{"role": "user", "content": "coder-m1"}]
    assert second.tester.initial_messages == [{"role": "user", "content": "tester-m1"}]
    assert second.reviewer.initial_messages == [{"role": "user", "content": "reviewer-m1"}]

    # 第二次执行应产出 4 条恢复事件（每角色一条）。
    events = repository.get_events(task.id)
    resume_events = [e for e in events if "检查点" in e["message"]]
    assert len(resume_events) == 4
    restored_agents = {e["data"]["agent"] for e in resume_events}
    assert restored_agents == {"planner", "coder", "tester", "reviewer"}
