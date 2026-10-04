"""任务队列三后端一致性测试（需要真实 MySQL / Redis 时才运行）。

队列存在三套实现（SQLite、MySQL、Redis），它们必须暴露同一套语义：
幂等入队、原子领取、租约续租、fencing 令牌、死信、回收与重置。
本模块用同一组断言逐个跑这三套实现，避免"某个后端悄悄跑偏"。

环境变量未配置时对应的用例自动跳过，因此离线环境仍可正常运行
``pytest``：

- ``TEST_MYSQL_URL``：例如 mysql://root:pass@127.0.0.1:33306/devpilot_test
- ``TEST_REDIS_URL``：例如 redis://127.0.0.1:36379/1
"""

import os
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from backend.src.config import settings
from backend.src.database import connection
from backend.src.database.task_repository import TaskRepository
from backend.src.exceptions import QueueFullError
from backend.src.models.agent_state import PlanStep
from backend.src.services.task_queue import RedisTaskQueue, SQLTaskQueue

MYSQL_URL = os.getenv("TEST_MYSQL_URL", "")
REDIS_URL = os.getenv("TEST_REDIS_URL", "")

_STEPS = [PlanStep(id=1, title="fix", description="fix")]


def _new_task(repo: TaskRepository, name: str) -> str:
    """! @brief 创建一条待执行任务，返回 task_id。"""

    task = repo.create_task(
        repo_path=f"E:/tmp/queue-test/{name}",
        question=f"fix {name}",
        plan=_STEPS,
    )
    return task.id


def _expire_lease(task_id: str) -> None:
    """! @brief 直接改写租约到期时间，避免测试里真实等待租约超时。"""

    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat(timespec="microseconds")
    with connection.get_connection() as conn:
        conn.execute(
            "UPDATE task_queue SET lease_expires_at = ? WHERE task_id = ?",
            (past, task_id),
        )


def _make_redis_expirer(queue: RedisTaskQueue):
    """! @brief 返回一个把 Redis 租约改成已过期的函数。

    Redis 的租约到期时间同时存在于哈希字段（可读）和租约索引分数（用于
    回收），两者都要改，否则 ``reclaim_expired`` 不会命中。
    """

    def expire(task_id: str) -> None:
        past_score = (datetime.now(UTC) - timedelta(seconds=1)).timestamp()
        queue._redis.hset(
            queue._key(task_id),
            "lease_expires_at",
            (datetime.now(UTC) - timedelta(seconds=1)).isoformat(timespec="microseconds"),
        )
        queue._redis.zadd(queue._LEASE_INDEX, {task_id: past_score})

    return expire


def _assert_queue_semantics(queue, repo: TaskRepository, expire_lease=None) -> None:
    """! @brief 三个后端共用的行为断言。

    覆盖：幂等入队、领取即自增尝试次数与令牌、fencing 拒绝、续租归属校验、
    成功收口幂等、失败回流与死信、过期租约隔离、以及 reset 重新入队。

    @param expire_lease 把某个任务的租约改成已过期；不同后端存储方式不同，
    缺省使用 SQL 队列的实现。
    """

    expire_lease = expire_lease or _expire_lease

    task_id = _new_task(repo, "semantics")

    # 幂等入队：task_id 唯一，重复入队返回 False 而不是抛异常。
    assert queue.enqueue(task_id, "key-1") is True
    assert queue.enqueue(task_id, "key-1") is False

    entry = queue.claim("worker-a")
    assert entry is not None and entry.task_id == task_id
    assert entry.attempts == 1
    assert entry.fence_token == 1

    # 已被领取，队列里不应再有可领取任务。
    assert queue.claim("worker-b") is None

    # fencing：只有当前持有者且令牌匹配才算有效。
    assert queue.is_current(task_id, "worker-a", entry.fence_token) is True
    assert queue.is_current(task_id, "worker-b", entry.fence_token) is False
    assert queue.is_current(task_id, "worker-a", entry.fence_token + 1) is False
    assert queue.heartbeat("worker-b", task_id, entry.fence_token) is False
    assert queue.heartbeat("worker-a", task_id, entry.fence_token) is True

    # 非持有者结算不生效。
    queue.complete(task_id, succeeded=True, worker_id="worker-b")
    assert queue.get(task_id).status == "claimed"

    queue.complete(
        task_id,
        succeeded=True,
        worker_id="worker-a",
        fence_token=entry.fence_token,
    )
    assert queue.get(task_id).status == "done"

    # 重复结算应幂等：done 不会被改回其它状态。
    queue.complete(task_id, succeeded=True, worker_id="worker-a")
    assert queue.get(task_id).status == "done"

    # reset 让终态任务可以重新执行。
    assert queue.reset(task_id, "key-2") is True
    assert queue.get(task_id).status == "queued"

    # 失败回流：首次失败回到 queued，尝试次数用尽后进入死信。
    failed = queue.claim("worker-a")
    assert failed is not None and failed.task_id == task_id
    queue.complete(task_id, succeeded=False, error="boom", worker_id="worker-a")
    assert queue.get(task_id).status == "queued"
    for _ in range(settings.task_max_attempts + 1):
        entry = queue.claim("worker-a")
        if entry is None:
            break
        queue.complete(task_id, succeeded=False, error="boom", worker_id="worker-a")
    assert queue.get(task_id).status == "dead"

    # 过期租约隔离为死信（而不是自动重跑），并清空持有者。
    stuck = _new_task(repo, "lease")
    assert queue.enqueue(stuck, "key-3") is True
    claimed = queue.claim("worker-a")
    assert claimed is not None and claimed.task_id == stuck
    expire_lease(stuck)
    queue.reclaim_expired()
    stuck_entry = queue.get(stuck)
    assert stuck_entry.status == "dead"
    assert stuck_entry.claimed_by is None
    # 隔离之后旧持有者不能再写入。
    assert queue.is_current(stuck, "worker-a", claimed.fence_token) is False
    assert queue.heartbeat("worker-a", stuck, claimed.fence_token) is False


def _assert_concurrent_claim_is_exclusive(queue, repo: TaskRepository) -> None:
    """! @brief 并发领取同一批任务时，每个任务只能被一个 Worker 拿到。"""

    task_ids = {_new_task(repo, f"concurrent-{index}") for index in range(8)}
    for task_id in task_ids:
        assert queue.enqueue(task_id, f"key-{task_id}") is True

    claimed: list[str] = []
    lock = threading.Lock()

    def worker(worker_id: str) -> None:
        while True:
            entry = queue.claim(worker_id)
            if entry is None:
                return
            with lock:
                claimed.append(entry.task_id)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    # 关键断言：没有任务被领取两次。
    assert sorted(claimed) == sorted(task_ids)


@pytest.fixture
def sqlite_queue(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> Iterator[tuple[SQLTaskQueue, TaskRepository]]:
    """! @brief SQLite 后端（默认配置，作为行为基准）。"""

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "queue.db")
    monkeypatch.setattr(settings, "database_backend", "sqlite")
    connection.init_database()
    yield SQLTaskQueue(), TaskRepository()


def test_sqlite_queue_semantics(
    sqlite_queue: tuple[SQLTaskQueue, TaskRepository],
) -> None:
    """SQLite 基准行为，离线即可运行。"""

    queue, repo = sqlite_queue
    _assert_queue_semantics(queue, repo)


def test_sqlite_concurrent_claim_is_exclusive(
    sqlite_queue: tuple[SQLTaskQueue, TaskRepository],
) -> None:
    """SQLite 单写者下并发领取不重复。"""

    queue, repo = sqlite_queue
    _assert_concurrent_claim_is_exclusive(queue, repo)


@pytest.mark.skipif(not MYSQL_URL, reason="未配置 TEST_MYSQL_URL")
def test_mysql_queue_semantics(monkeypatch: MonkeyPatch) -> None:
    """MySQL 后端必须与 SQLite 语义一致。"""

    monkeypatch.setattr(settings, "database_backend", "mysql")
    monkeypatch.setattr(settings, "mysql_url", _secret(MYSQL_URL))
    connection.init_database()
    _drop_queue_rows()
    queue, repo = SQLTaskQueue(), TaskRepository()
    _assert_queue_semantics(queue, repo)


@pytest.mark.skipif(not MYSQL_URL, reason="未配置 TEST_MYSQL_URL")
def test_mysql_concurrent_claim_is_exclusive(monkeypatch: MonkeyPatch) -> None:
    """MySQL 靠 FOR UPDATE SKIP LOCKED 保证并发领取不重复。"""

    monkeypatch.setattr(settings, "database_backend", "mysql")
    monkeypatch.setattr(settings, "mysql_url", _secret(MYSQL_URL))
    connection.init_database()
    _drop_queue_rows()
    queue, repo = SQLTaskQueue(), TaskRepository()
    _assert_concurrent_claim_is_exclusive(queue, repo)


@pytest.mark.skipif(not REDIS_URL, reason="未配置 TEST_REDIS_URL")
def test_redis_queue_semantics(
    monkeypatch: MonkeyPatch, sqlite_queue: tuple[SQLTaskQueue, TaskRepository],
) -> None:
    """Redis（Lua 原子实现）必须与 SQL 版语义一致。"""

    monkeypatch.setattr(settings, "redis_url", REDIS_URL)
    queue, repo = RedisTaskQueue(), sqlite_queue[1]
    _flush(queue)
    try:
        _assert_queue_semantics(queue, repo, _make_redis_expirer(queue))
    finally:
        _flush(queue)


@pytest.mark.skipif(not REDIS_URL, reason="未配置 TEST_REDIS_URL")
def test_redis_concurrent_claim_is_exclusive(
    monkeypatch: MonkeyPatch, sqlite_queue: tuple[SQLTaskQueue, TaskRepository],
) -> None:
    """Lua 脚本在 Redis 单线程内执行，领取必须互斥且不丢条目。"""

    monkeypatch.setattr(settings, "redis_url", REDIS_URL)
    queue, repo = RedisTaskQueue(), sqlite_queue[1]
    _flush(queue)
    try:
        _assert_concurrent_claim_is_exclusive(queue, repo)
    finally:
        _flush(queue)


@pytest.mark.skipif(not REDIS_URL, reason="未配置 TEST_REDIS_URL")
def test_redis_enqueue_depth_backpressure(
    monkeypatch: MonkeyPatch, sqlite_queue: tuple[SQLTaskQueue, TaskRepository],
) -> None:
    """队列深度上限生效时抛 QueueFullError。"""

    monkeypatch.setattr(settings, "redis_url", REDIS_URL)
    monkeypatch.setattr(settings, "task_queue_max_depth", 2)
    queue, repo = RedisTaskQueue(), sqlite_queue[1]
    _flush(queue)
    try:
        for index in range(2):
            assert queue.enqueue(_new_task(repo, f"depth-{index}"), f"k{index}") is True
        with pytest.raises(QueueFullError):
            queue.enqueue(_new_task(repo, "depth-over"), "k-over")
    finally:
        _flush(queue)


def _secret(value: str):
    """! @brief 把明文连接串包成 SecretStr，避免测试里重复 import。"""

    from pydantic import SecretStr

    return SecretStr(value)


def _drop_queue_rows() -> None:
    """! @brief 清空 MySQL 里的队列行，让每个用例从干净状态开始。"""

    with connection.get_connection() as conn:
        conn.execute("DELETE FROM task_queue")


def _flush(queue: RedisTaskQueue) -> None:
    """! @brief 清掉本测试用到的 Redis 键。

    只删除 ``devpilot:tq:*`` 前缀的键，避免影响同一 Redis 实例上的其它数据。
    """

    for key in queue._redis.scan_iter(match="devpilot:tq:*"):
        queue._redis.delete(key)


@pytest.fixture(params=["sqlite", "mysql", "redis"])
def queue_backend(request, tmp_path, monkeypatch):
    """! @brief 用同一组边界用例验证三后端。"""
    from backend.src.database.connection import _MySQLPool
    monkeypatch.setattr(connection, "_MYSQL_POOL", _MySQLPool())
    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "edge.db")
    monkeypatch.setattr(settings, "database_backend", "sqlite")
    if request.param == "mysql":
        if not MYSQL_URL:
            pytest.skip("未配置 TEST_MYSQL_URL")
        monkeypatch.setattr(settings, "database_backend", "mysql")
        monkeypatch.setattr(settings, "mysql_url", _secret(MYSQL_URL))
    connection.init_database()
    if request.param == "redis":
        if not REDIS_URL:
            pytest.skip("未配置 TEST_REDIS_URL")
        monkeypatch.setattr(settings, "redis_url", REDIS_URL)
        queue = RedisTaskQueue()
        _flush(queue)
        expire = _make_redis_expirer(queue)
    else:
        _drop_queue_rows()
        queue, expire = SQLTaskQueue(), _expire_lease
    try:
        yield queue, TaskRepository(), expire
    finally:
        if request.param == "redis":
            _flush(queue)
        else:
            _drop_queue_rows()


def test_priority_stats_and_unexpired_leases(queue_backend):
    """! @brief 高优先级先领；有效租约不回收；所有状态可统计。"""
    queue, repo, expire = queue_backend
    low, high = _new_task(repo, "low"), _new_task(repo, "high")
    queue.enqueue(low, "low", priority=0)
    queue.enqueue(high, "high", priority=10)
    entry = queue.claim("worker")
    assert entry.task_id == high
    assert queue.reclaim_expired() == 0
    assert queue.is_current(high, "worker", entry.fence_token)
    assert queue.stats()["claimed"] == 1
    queue.complete(high, True, worker_id="worker", fence_token=entry.fence_token)
    assert queue.stats()["done"] == 1
    assert queue.stats()["queued"] == 1


def test_expiry_is_rejected_before_reclaim_and_updates_task(queue_backend):
    """! @brief 无需等待回收器，过期租约就不能续租、结算或写业务状态。"""
    queue, repo, expire = queue_backend
    task_id = _new_task(repo, "expired")
    queue.enqueue(task_id, "expire")
    entry = queue.claim("worker")
    repo.claim_status(task_id, {"awaiting_approval"}, "running")
    expire(task_id)
    assert not queue.is_current(task_id, "worker", entry.fence_token)
    assert not queue.heartbeat("worker", task_id, entry.fence_token)
    queue.complete(task_id, True, worker_id="worker", fence_token=entry.fence_token)
    assert queue.get(task_id).status == "claimed"
    assert queue.reclaim_expired() == 1
    assert queue.get(task_id).status == "dead"
    assert repo.get_task(task_id).status == "interrupted"


def test_full_queue_retry_and_reset_semantics(queue_backend, monkeypatch):
    """! @brief 恢复受容量限制；在途失败重试保留，不因积压丢进死信。"""
    queue, repo, expire = queue_backend
    monkeypatch.setattr(settings, "task_queue_max_depth", 1)
    first, second = _new_task(repo, "first"), _new_task(repo, "second")
    queue.enqueue(first, "first")
    entry = queue.claim("worker")
    queue.enqueue(second, "second")
    assert queue.enqueue(second, "second") is False
    queue.complete(first, False, worker_id="worker", fence_token=entry.fence_token)
    assert queue.get(first).status == "queued"
    queue.remove(second)
    queue.remove(first)
    queue.enqueue(first, "finish")
    entry = queue.claim("worker")
    queue.complete(first, True, worker_id="worker", fence_token=entry.fence_token)
    queue.enqueue(second, "full")
    with pytest.raises(QueueFullError):
        queue.reset(first, "resume")
    assert queue.get(first).status == "done"


def test_remove_does_not_delete_claimed_task(queue_backend):
    """! @brief 排队取消与领取竞争时，不删除 Worker 的有效租约。"""
    queue, repo, expire = queue_backend
    task_id = _new_task(repo, "remove")
    queue.enqueue(task_id, "remove")
    entry = queue.claim("worker")
    assert queue.remove(task_id) is False
    assert queue.is_current(task_id, "worker", entry.fence_token)


@pytest.mark.skipif(not MYSQL_URL, reason="未配置 TEST_MYSQL_URL")
def test_mysql_api_queue_worker_sse_end_to_end(tmp_path, monkeypatch):
    """! @brief 真实 MySQL 上 API 入队→独立 Worker→持久事件→SSE 完整链路。"""
    from fastapi.testclient import TestClient
    from backend.src import main
    from backend.src.models.agent_state import AgentEvent
    from backend.src.services.task_execution_service import TaskExecutionService
    from backend.src.worker import run_worker_once

    monkeypatch.setattr(settings, "database_backend", "mysql")
    monkeypatch.setattr(settings, "mysql_url", _secret(MYSQL_URL))
    monkeypatch.setattr(settings, "task_queue_backend", "sqlite")
    connection.init_database()
    _drop_queue_rows()
    queue, repo = SQLTaskQueue(), TaskRepository()
    started = threading.Event()

    class Runner:
        def execute_stream(self, **kwargs):
            yield AgentEvent(type="start", agent="coder", message="started")
            yield AgentEvent(type="final", agent="orchestrator", message="done")

    service = TaskExecutionService(repo, lambda *args: Runner())

    def consume():
        import time
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if run_worker_once(queue, service, "mysql-e2e"):
                started.set()
                return
            time.sleep(0.01)

    with TestClient(main.app) as client:
        task = repo.create_task(repo_path=str(tmp_path), question="fix", plan=_STEPS)
        worker = threading.Thread(target=consume)
        worker.start()
        response = client.post(f"/api/tasks/{task.id}/execute")
        worker.join(5)
        assert started.is_set()
        assert response.status_code == 200
        assert '"type": "final"' in response.text
        assert client.get(f"/api/tasks/{task.id}").json()["task"]["status"] == "completed"
        assert queue.get(task.id).status == "done"


@pytest.mark.skipif(not MYSQL_URL, reason="未配置 TEST_MYSQL_URL")
def test_sqlite_migration_to_empty_mysql_and_refusal_to_overwrite(tmp_path, monkeypatch):
    """! @brief 停机迁移保存状态、事件和检查点；非空目标不得覆盖。"""
    from uuid import uuid4
    from urllib.parse import urlsplit, urlunsplit
    from backend.scripts.migrate_sqlite_to_mysql import migrate, TABLES
    from backend.src.database.connection import _MySQLPool
    from backend.src.models.agent_state import AgentEvent
    from backend.src.services import context_store

    monkeypatch.setattr(settings, "database_backend", "sqlite")
    source_path = tmp_path / "migration.db"
    monkeypatch.setattr(connection, "DATABASE_PATH", source_path)
    connection.init_database()
    repo = TaskRepository()
    task = repo.create_task(repo_path=str(tmp_path), question="迁移中文任务", plan=_STEPS)
    repo.add_event(task.id, 1, AgentEvent(type="final", agent="coder", message="中文事件"))
    context_store.save_context(task.id, "coder", [{"role": "user", "content": "中文上下文"}])
    repo.claim_status(task.id, {"awaiting_approval"}, "completed")
    expected = {}
    with connection.get_connection() as conn:
        for table in TABLES:
            expected[table] = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]

    monkeypatch.setattr(settings, "database_backend", "mysql")
    monkeypatch.setattr(settings, "mysql_url", _secret(MYSQL_URL))
    database = "devpilot_migration_" + uuid4().hex
    admin = connection._connect_mysql()
    admin.cursor().execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
    parts = urlsplit(MYSQL_URL)
    monkeypatch.setattr(settings, "mysql_url", _secret(urlunsplit(parts._replace(path="/" + database))))
    pool = _MySQLPool()
    monkeypatch.setattr(connection, "_MYSQL_POOL", pool)
    try:
        original = connection.MySQLConnection.executemany
        def fail_after_tasks(self, sql, rows):
            if "plan_steps" in sql:
                raise RuntimeError("injected migration failure")
            return original(self, sql, rows)
        with monkeypatch.context() as fault:
            fault.setattr(connection.MySQLConnection, "executemany", fail_after_tasks)
            with pytest.raises(RuntimeError, match="injected"):
                migrate(source_path, batch_size=1)
        with connection.get_connection() as conn:
            for table in TABLES:
                assert conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0
        assert migrate(source_path, batch_size=1) == expected
        assert repo.get_task(task.id).question == "迁移中文任务"
        assert repo.get_events(task.id)[0]["message"] == "中文事件"
        assert context_store.load_context(task.id, "coder")[0]["content"] == "中文上下文"
        with pytest.raises(RuntimeError, match="非空"):
            migrate(source_path)
    finally:
        for raw in pool._idle:
            raw.close()
        pool._idle.clear()
        admin.cursor().execute(f"DROP DATABASE `{database}`")
        admin.close()
