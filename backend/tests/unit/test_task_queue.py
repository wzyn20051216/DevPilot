"""任务队列与 Worker 领取执行逻辑测试（离线，不依赖 Redis/Docker/网络）。"""

import threading
from datetime import UTC, datetime, timedelta
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from backend.src.config import settings
from backend.src.database import connection
from backend.src.database.task_repository import TaskRepository
from backend.src.models.agent_state import AgentEvent, PlanStep
from backend.src.services.task_execution_service import TaskExecutionService
from backend.src.services.task_queue import (
    SQLiteTaskQueue,
    get_task_queue,
)
from backend.src.worker import run_worker_once


def _create_task(repository: TaskRepository):
    """! @brief 用最小计划步骤创建一条 awaiting_approval 任务。"""
    return repository.create_task(
        repo_path="repo",
        question="fix",
        plan=[PlanStep(id=1, title="fix", description="fix")],
    )


def _init_db(tmp_path: Path, monkeypatch: MonkeyPatch) -> TaskRepository:
    """! @brief 把连接切到临时库并初始化，返回仓储。"""
    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "tasks.db")
    connection.init_database()
    return TaskRepository()


def _expire_lease(task_id: str) -> None:
    """! @brief 直接改写数据库中的租约到期时间，消除墙钟睡眠抖动。"""
    with connection.get_connection() as conn:
        conn.execute(
            """
            UPDATE task_queue
            SET lease_expires_at = ?
            WHERE task_id = ?
            """,
            (
                (datetime.now(UTC) - timedelta(seconds=1)).isoformat(
                    timespec="microseconds"
                ),
                task_id,
            ),
        )


def test_enqueue_is_idempotent(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """重复入队同一 task_id 应返回 False 而不是抛错。"""

    repository = _init_db(tmp_path, monkeypatch)
    task = _create_task(repository)
    queue = SQLiteTaskQueue()

    assert queue.enqueue(task.id, "key-1") is True
    assert queue.enqueue(task.id, "key-1") is False

    entry = queue.get(task.id)
    assert entry is not None
    assert entry.status == "queued"
    # 未显式传 max_attempts 时使用全局默认值。
    assert entry.max_attempts == settings.task_max_attempts


def test_concurrent_claim_single_winner(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """两个 Worker 并发领取同一任务，只能有一个成功。"""

    repository = _init_db(tmp_path, monkeypatch)
    task = _create_task(repository)
    queue = SQLiteTaskQueue()
    assert queue.enqueue(task.id, "key-1") is True

    barrier = threading.Barrier(2)
    claimed: list[str] = []

    def claim(worker_id: str) -> None:
        barrier.wait()
        entry = queue.claim(worker_id)
        if entry is not None:
            claimed.append(entry.task_id)

    threads = [
        threading.Thread(target=claim, args=(f"worker-{index}",))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # 单条 UPDATE 内嵌子查询的原子领取保证只有一个赢家。
    assert claimed == [task.id]
    entry = queue.get(task.id)
    assert entry is not None
    assert entry.status == "claimed"
    assert entry.attempts == 1


def test_priority_higher_claimed_first(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """高优先级任务应先被领取。"""

    repository = _init_db(tmp_path, monkeypatch)
    low = _create_task(repository)
    high = _create_task(repository)
    queue = SQLiteTaskQueue()
    assert queue.enqueue(low.id, "key-low", priority=0) is True
    assert queue.enqueue(high.id, "key-high", priority=10) is True

    entry = queue.claim("worker-1")
    assert entry is not None
    assert entry.task_id == high.id


def test_lease_expiry_allows_reclaim(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """租约到期后隔离，禁止其他 Worker 自动抢占可能仍在执行的任务。"""

    repository = _init_db(tmp_path, monkeypatch)
    task = _create_task(repository)
    queue = SQLiteTaskQueue()
    assert queue.enqueue(task.id, "key-1") is True

    assert queue.claim("worker-1") is not None
    # 租约未到期：reclaim 不回收，worker-2 领不到。
    assert queue.reclaim_expired() == 0
    assert queue.claim("worker-2") is None

    # 直接把租约改写为已过期，模拟时间流逝（避免睡眠抖动）。
    _expire_lease(task.id)
    assert queue.reclaim_expired() == 1
    assert queue.claim("worker-2") is None
    assert queue.get(task.id).status == "dead"
    # 失去租约的旧 Worker 不能覆盖隔离结果。
    queue.complete(task.id, succeeded=True, worker_id="worker-1")
    assert queue.get(task.id).status == "dead"


def test_heartbeat_renews_lease(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """只有持有者能续租；续租会真实推后租约到期时间。"""

    repository = _init_db(tmp_path, monkeypatch)
    task = _create_task(repository)
    queue = SQLiteTaskQueue()
    assert queue.enqueue(task.id, "key-1") is True
    assert queue.claim("worker-1") is not None

    # 非持有者续租失败。
    assert queue.heartbeat("worker-2", task.id) is False
    assert queue.heartbeat("worker-1", task.id) is True

    # 续租后租约到期时间应被推后到未来。
    with connection.get_connection() as conn:
        row = conn.execute(
            "SELECT lease_expires_at FROM task_queue WHERE task_id = ?",
            (task.id,),
        ).fetchone()
    renewed_at = datetime.fromisoformat(str(row["lease_expires_at"]))
    assert renewed_at > datetime.now(UTC)


def test_complete_success_and_failure_retry(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """成功进 done；失败未达上限回 queued，达到上限进 dead。"""

    repository = _init_db(tmp_path, monkeypatch)
    task = _create_task(repository)
    queue = SQLiteTaskQueue()
    assert queue.enqueue(task.id, "key-1", max_attempts=2) is True

    assert queue.claim("worker-1") is not None
    queue.complete(task.id, succeeded=True)
    assert queue.get(task.id).status == "done"

    # 重置后模拟失败路径。
    assert queue.reset(task.id, "key-1") is True
    assert queue.claim("worker-1") is not None
    queue.complete(task.id, succeeded=False, error="boom")
    entry = queue.get(task.id)
    # attempts=1 < max_attempts=2 → 回 queued 并保留 last_error。
    assert entry.status == "queued"
    assert entry.last_error == "boom"

    assert queue.claim("worker-1") is not None
    queue.complete(task.id, succeeded=False, error="boom again")
    entry = queue.get(task.id)
    # attempts=2 已达上限 → 死信。
    assert entry.status == "dead"
    assert entry.last_error == "boom again"


def test_run_worker_once_end_to_end(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """入队 → run_worker_once → 任务完成且队列收口为 done。"""

    repository = _init_db(tmp_path, monkeypatch)
    task = _create_task(repository)

    class FinishedOrchestrator:
        def execute_stream(
            self, question: str, plan: list
        ) -> Iterator[AgentEvent]:
            yield AgentEvent(type="start", agent="coder", message="start")
            yield AgentEvent(type="final", agent="orchestrator", message="done")

    service = TaskExecutionService(
        repository,
        lambda repo_path, cancel_check: FinishedOrchestrator(),
    )
    queue = SQLiteTaskQueue()
    assert queue.enqueue(task.id, "key-1") is True

    assert run_worker_once(queue, service, "worker-1") is True
    assert repository.get_task(task.id).status == "completed"
    assert queue.get(task.id).status == "done"
    # 队列已空，再次运行返回 False。
    assert run_worker_once(queue, service, "worker-1") is False


def test_run_worker_once_retries_until_dead(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """反复失败的任务应按 attempts 上限进入死信，任务状态为 failed。"""

    repository = _init_db(tmp_path, monkeypatch)
    task = _create_task(repository)

    calls = []

    class ExplodingOrchestrator:
        def execute_stream(
            self, question: str, plan: list
        ) -> Iterator[AgentEvent]:
            calls.append(question)
            yield AgentEvent(type="error", agent="coder", message="永远失败")

    service = TaskExecutionService(
        repository,
        lambda repo_path, cancel_check: ExplodingOrchestrator(),
    )
    queue = SQLiteTaskQueue()
    assert queue.enqueue(task.id, "key-1", max_attempts=2) is True

    # 第一次执行：失败，attempts=1 < 2 → 回 queued。
    assert run_worker_once(queue, service, "worker-1") is True
    assert queue.get(task.id).status == "queued"
    assert repository.get_task(task.id).status == "failed"

    # 第二次执行：失败，attempts=2 达上限 → dead。
    assert run_worker_once(queue, service, "worker-1") is True
    assert queue.get(task.id).status == "dead"
    assert repository.get_task(task.id).status == "failed"
    assert calls == ["fix", "fix"]


def test_get_task_queue_inline_raises(
    monkeypatch: MonkeyPatch,
) -> None:
    """inline 模式不需要队列，工厂应直接拒绝。"""

    monkeypatch.setattr(settings, "task_queue_backend", "inline")
    with pytest.raises(ValueError):
        get_task_queue()


def test_reset_recycles_terminal_entries_only(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """reset 应只回收 done/dead 记录，不破坏 queued/claimed 的幂等保护。"""

    repository = _init_db(tmp_path, monkeypatch)
    task = _create_task(repository)
    queue = SQLiteTaskQueue()

    assert queue.enqueue(task.id, "key-a") is True
    # queued 状态不允许 reset（还在排队，重置会破坏幂等语义）。
    assert queue.reset(task.id, "key-a-2") is False

    queue.claim("worker-1")
    queue.complete(task.id, succeeded=True)
    assert queue.get(task.id).status == "done"
    assert queue.reset(task.id, "key-a-2") is True
    entry = queue.get(task.id)
    assert entry.status == "queued"
    assert entry.attempts == 0
    assert entry.idempotency_key == "key-a-2"

    queue.claim("worker-1")
    assert queue.reset(task.id, "key-a-3") is False


def test_remove_deletes_queued_entry(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """remove 应删除队列记录，用于排队中取消场景。"""

    repository = _init_db(tmp_path, monkeypatch)
    task = _create_task(repository)
    queue = SQLiteTaskQueue()

    assert queue.enqueue(task.id, "key-b") is True
    assert queue.remove(task.id) is True
    assert queue.get(task.id) is None
    # 重复 remove 幂等返回 False，之后可以重新入队。
    assert queue.remove(task.id) is False
    assert queue.enqueue(task.id, "key-b-2") is True


def test_expired_running_task_is_quarantined(tmp_path, monkeypatch):
    """! @brief 租约丢失必须同步任务状态，且禁止自动重投写代码任务。"""
    repository = _init_db(tmp_path, monkeypatch)
    task = _create_task(repository)
    queue = SQLiteTaskQueue()
    queue.enqueue(task.id, "lease")
    queue.claim("old-worker")
    repository.claim_status(task.id, {"awaiting_approval"}, "running")
    _expire_lease(task.id)
    assert queue.claim("new-worker") is None
    assert repository.get_task(task.id).status == "interrupted"
    assert queue.get(task.id).status == "dead"
    assert not queue.heartbeat("old-worker", task.id)


def test_completion_requires_claim_owner(tmp_path, monkeypatch):
    """! @brief 其他 Worker 不能结算当前租约。"""
    repository = _init_db(tmp_path, monkeypatch)
    task = _create_task(repository)
    queue = SQLiteTaskQueue()
    queue.enqueue(task.id, "owner")
    queue.claim("new")
    queue.complete(task.id, succeeded=True, worker_id="old")
    assert queue.get(task.id).status == "claimed"
    queue.complete(task.id, succeeded=True, worker_id="new")
    assert queue.get(task.id).status == "done"


def test_remove_does_not_delete_active_claim(tmp_path, monkeypatch):
    """! @brief 排队取消与领取竞争时不能删除正在执行的租约。"""
    repository = _init_db(tmp_path, monkeypatch)
    task = _create_task(repository)
    queue = SQLiteTaskQueue()
    queue.enqueue(task.id, "cancel-race")
    queue.claim("worker")
    assert not queue.remove(task.id)
    assert queue.get(task.id).status == "claimed"


def test_full_queue_allows_idempotent_enqueue_but_rejects_reset(tmp_path, monkeypatch):
    """! @brief 幂等重试不受深度限制；恢复历史任务仍须检查容量。"""
    from backend.src.exceptions import QueueFullError
    repo = _init_db(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "task_queue_max_depth", 1)
    queue = SQLiteTaskQueue()
    first, other = _create_task(repo), _create_task(repo)
    assert queue.enqueue(first.id, "first")
    entry = queue.claim("worker")
    queue.complete(first.id, True, worker_id="worker", fence_token=entry.fence_token)
    assert queue.enqueue(other.id, "other")
    assert queue.enqueue(other.id, "other") is False
    with pytest.raises(QueueFullError):
        queue.reset(first.id, "resume")
    assert queue.get(first.id).status == "done"
