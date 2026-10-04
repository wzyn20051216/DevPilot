"""后台任务状态、幂等领取与取消测试。"""

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from pytest import MonkeyPatch

from backend.src.database import connection
from backend.src.database.task_repository import TaskRepository
from backend.src.models.agent_state import AgentEvent, PlanStep
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
    timeout: float = 2.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if repository.get_task(task_id).status == expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"任务未进入 {expected}")


def test_task_claim_is_atomic(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """两个并发请求只能有一个把待审批任务领取为 running。"""

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "tasks.db")
    connection.init_database()
    repository = TaskRepository()
    task = _create_task(repository)
    barrier = threading.Barrier(3)
    results: list[bool] = []

    def claim() -> None:
        barrier.wait()
        results.append(
            repository.claim_status(task.id, {"awaiting_approval"}, "running")
        )

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sorted(results) == [False, True]
    assert repository.get_task(task.id).status == "running"


def test_background_execution_does_not_require_sse_consumer(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """即使没有客户端读取 SSE，后台任务也应完成并持久化事件。"""

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "tasks.db")
    connection.init_database()
    repository = TaskRepository()
    task = _create_task(repository)

    class FinishedOrchestrator:
        def execute_stream(self, question: str, plan: list) -> Iterator[AgentEvent]:
            yield AgentEvent(type="start", agent="coder", message="start")
            yield AgentEvent(type="final", agent="orchestrator", message="done")

    service = TaskExecutionService(
        repository,
        lambda repo_path, cancel_check: FinishedOrchestrator(),
    )
    assert repository.claim_status(task.id, {"awaiting_approval"}, "running")
    service.start(task.id)

    _wait_for_status(repository, task.id, "completed")

    assert [event["type"] for event in repository.get_events(task.id)] == [
        "start",
        "final",
    ]


def test_running_task_can_be_cancelled(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """取消信号应终止后续 Agent 工作并留下明确的 cancelled 状态。"""

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "tasks.db")
    connection.init_database()
    repository = TaskRepository()
    task = _create_task(repository)
    entered = threading.Event()

    class WaitingOrchestrator:
        def __init__(self, cancel_check: Callable[[], bool]) -> None:
            self.cancel_check = cancel_check

        def execute_stream(self, question: str, plan: list) -> Iterator[AgentEvent]:
            entered.set()
            yield AgentEvent(type="start", agent="coder", message="start")
            while not self.cancel_check():
                time.sleep(0.005)
            yield AgentEvent(type="cancelled", agent="coder", message="cancelled")

    service = TaskExecutionService(
        repository,
        lambda repo_path, cancel_check: WaitingOrchestrator(cancel_check),
    )
    assert repository.claim_status(task.id, {"awaiting_approval"}, "running")
    service.start(task.id)
    assert entered.wait(timeout=1)
    assert repository.claim_status(task.id, {"running"}, "cancelling")
    assert service.cancel(task.id) is True

    _wait_for_status(repository, task.id, "cancelled")

    assert repository.get_events(task.id)[-1]["type"] == "cancelled"
    assert sum(
        event["type"] == "cancelled" for event in repository.get_events(task.id)
    ) == 1


def test_lost_lease_stops_writes_and_leaves_state_alone(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """失去租约的执行者必须停止写入，且不得覆盖最终状态。

    复现真实场景：Worker A 领取任务后租约过期、被队列隔离，随后 Worker B
    接管。此时 A 的后续事件和收口都不应落盘——否则会把 B 的进度覆盖掉。
    """

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "tasks.db")
    connection.init_database()
    repository = TaskRepository()
    task = _create_task(repository)
    assert repository.claim_status(task.id, {"awaiting_approval"}, "running")

    # 第一个事件写入后才失去租约，用来验证"失去之后不再写"。
    fence = {"held": True, "checks": 0}
    # 记录编排器被消费到哪一步：fencing 生效时循环会提前 break，
    # 生成器不会再被推进，因此这里停在第一个 yield 之后。
    produced: list[str] = []

    class SlowOrchestrator:
        def execute_stream(self, question: str, plan: list) -> Iterator[AgentEvent]:
            produced.append("start")
            yield AgentEvent(type="start", agent="coder", message="start")
            # 交出控制权后租约失效：下一次事件写入前应被拦下。
            fence["held"] = False
            produced.append("final")
            yield AgentEvent(type="final", agent="orchestrator", message="done")

    def fence_check() -> bool:
        fence["checks"] += 1
        return bool(fence["held"])

    service = TaskExecutionService(
        repository,
        lambda repo_path, cancel_check: SlowOrchestrator(),
    )
    service.start(task.id, fence_check)

    # 等执行线程结束（fencing 触发后应当很快退出）。
    deadline = time.monotonic() + 2
    while service.is_running(task.id) and time.monotonic() < deadline:
        time.sleep(0.01)

    # 只有失去租约前的事件被持久化；final 从未落盘。
    assert [event["type"] for event in repository.get_events(task.id)] == ["start"]
    assert fence["checks"] > 0
    # 旧执行者没有改最终状态，任务仍留给新持有者处理。
    assert repository.get_task(task.id).status == "running"


def test_startup_marks_orphaned_execution_interrupted(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """服务重启后不能让历史任务永久显示 running。"""

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "tasks.db")
    connection.init_database()
    repository = TaskRepository()
    task = _create_task(repository)
    repository.set_status(task.id, "running")

    assert repository.mark_incomplete_as_interrupted() == 1
    assert repository.get_task(task.id).status == "interrupted"


def test_inline_capacity_is_atomic_and_covers_resume(tmp_path, monkeypatch):
    """! @brief 并发 execute/resume 共享容量；拒绝时不改变持久化状态。"""
    from backend.src.config import settings
    from backend.src.exceptions import QueueFullError

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "capacity.db")
    monkeypatch.setattr(settings, "task_inline_max_running", 1)
    connection.init_database()
    repository = TaskRepository()
    tasks = [_create_task(repository) for _ in range(2)]
    repository.claim_status(tasks[1].id, {"awaiting_approval"}, "interrupted")
    release = threading.Event()
    barrier = threading.Barrier(3)
    accepted, rejected = [], []

    class BlockingRunner:
        def execute_stream(self, **kwargs):
            assert release.wait(5)
            yield AgentEvent(type="final", agent="orchestrator", message="done")

    service = TaskExecutionService(repository, lambda *args: BlockingRunner())

    def request(task, expected):
        barrier.wait()
        try:
            service.start_inline(task.id, expected)
            accepted.append(task.id)
        except QueueFullError:
            rejected.append((task.id, expected))

    threads = [threading.Thread(target=request, args=(t, status))
               for t, status in zip(tasks, ["awaiting_approval", "interrupted"])]
    try:
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(3)
        assert len(accepted) == len(rejected) == 1
        assert service.running_count() == 1
        task_id, expected = rejected[0]
        assert repository.get_task(task_id).status == expected
    finally:
        release.set()
        for thread in list(service._threads.values()):
            thread.join(3)


def test_inline_thread_start_failure_restores_original_status(tmp_path, monkeypatch):
    """! @brief 线程启动失败不遗留 running 状态或容量占位。"""
    import pytest
    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "failure.db")
    connection.init_database()
    repository = TaskRepository()
    task = _create_task(repository)
    service = TaskExecutionService(repository, lambda *args: None)

    def fail_start(*args):
        raise RuntimeError("thread unavailable")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    with pytest.raises(RuntimeError, match="thread unavailable"):
        service.start_inline(task.id, "awaiting_approval")
    assert repository.get_task(task.id).status == "awaiting_approval"
    assert service.running_count() == 0


def test_fence_loss_during_exception_or_finalization_does_not_overwrite(tmp_path, monkeypatch):
    """! @brief Agent 抛错和生成器结束两个收尾路径均不能写过期状态。"""
    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "fence-tail.db")
    connection.init_database()
    repository = TaskRepository()
    for throws in [False, True]:
        task = _create_task(repository)
        repository.claim_status(task.id, {"awaiting_approval"}, "running")
        fence = {"held": True}

        class Runner:
            def execute_stream(self, **kwargs):
                yield AgentEvent(type="start", agent="coder", message="start")
                fence["held"] = False
                if throws:
                    raise RuntimeError("crash after lease loss")

        service = TaskExecutionService(repository, lambda *args: Runner())
        service.start(task.id, lambda: fence["held"])
        thread = service._threads.get(task.id)
        if thread:
            thread.join(3)
        assert repository.get_task(task.id).status == "running"
        assert [event["type"] for event in repository.get_events(task.id)] == ["start"]
