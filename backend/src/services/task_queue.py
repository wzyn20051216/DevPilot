"""! @brief 持久化任务队列（技术手册 10.2.5）。

本模块把「任务入队」与「任务执行」解耦：API 进程只负责把待执行任务写入
队列，独立的 Sandbox Worker 进程负责领取并执行。队列通过三组机制保证
在多个 Worker 并发、Worker 崩溃、重复入队等场景下的正确性：

- **租约（lease）**：领取任务时写入 ``lease_expires_at``，Worker 崩溃后
  SQLite 租约到期后隔离任务，确认旧进程停止后显式恢复；
- **心跳（heartbeat）**：存活 Worker 周期性续租，避免长任务被误判为
  崩溃而重复执行；
- **幂等（idempotency）**：入队靠 ``task_id`` 唯一约束去重；执行靠
  ``attempts`` 计数 + ``max_attempts`` 上限把反复失败的任务送进死信。

后端可用 ``settings.task_queue_backend`` 在 SQLite 与 Redis 之间切换；
SQLite 队列不需额外服务；Redis 是非原子、未经生产验收的实验实现。默认执行方式仍为 inline。
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..config import settings
from ..database.connection import get_connection


def _now_iso() -> str:
    """! @brief 返回微秒精度的 UTC ISO 时间字符串。

    队列的租约/心跳比较依赖时间先后。保留微秒精度能让短租约（测试与
    生产调度）的判断更精确；同一函数生成的字符串格式一致、长度固定，
    因而可以直接按字典序比较两个时间戳的先后。
    """
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _lease_expiry(now: str) -> str:
    """! @brief 从当前时间戳推导租约到期时间。"""
    return (
        datetime.fromisoformat(now) + timedelta(seconds=settings.task_worker_lease_seconds)
    ).isoformat(timespec="microseconds")


@dataclass
class QueueEntry:
    """! @brief ``task_queue`` 表一行的内存映射。

    列含义与 ``connection.init_database`` 中的建表语句保持一致；
    ``attempts`` / ``max_attempts`` / ``priority`` / ``id`` 为整型，
    其余字段为字符串，可空字段为 ``str | None``。
    """

    id: int
    task_id: str
    idempotency_key: str
    status: str
    priority: int
    attempts: int
    max_attempts: int
    claimed_by: str | None
    lease_expires_at: str | None
    heartbeat_at: str | None
    last_error: str | None
    created_at: str
    updated_at: str


def _row_to_entry(row: sqlite3.Row) -> QueueEntry:
    """! @brief 把 SQLite 行转换为 QueueEntry，统一做显式类型收窄。"""
    return QueueEntry(
        id=int(row["id"]),
        task_id=str(row["task_id"]),
        idempotency_key=str(row["idempotency_key"]),
        status=str(row["status"]),
        priority=int(row["priority"]),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        claimed_by=row["claimed_by"],
        lease_expires_at=row["lease_expires_at"],
        heartbeat_at=row["heartbeat_at"],
        last_error=row["last_error"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


class SQLiteTaskQueue:
    """! @brief 基于 SQLite 的持久化任务队列。

    SQLite 同一时刻只有一个写者，配合「单条 UPDATE 语句内嵌子查询领取」
    的方式避免重复领取；跨表回收和结算仍使用显式事务。
    """

    def enqueue(
        self,
        task_id: str,
        idempotency_key: str,
        priority: int = 0,
        max_attempts: int | None = None,
    ) -> bool:
        """! @brief 把任务写入队列。

        @return True 表示首次入队成功；task_id 已存在时返回 False（幂等，
        不抛异常）。max_attempts 缺省时取全局 ``settings.task_max_attempts``。
        """
        if max_attempts is None:
            max_attempts = settings.task_max_attempts
        now = _now_iso()
        try:
            with get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO task_queue (
                        task_id,
                        idempotency_key,
                        status,
                        priority,
                        attempts,
                        max_attempts,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, 'queued', ?, 0, ?, ?, ?)
                    """,
                    (
                        task_id,
                        idempotency_key,
                        priority,
                        max_attempts,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError:
            # task_id 有 UNIQUE 约束，重复入队只会触发 IntegrityError。
            # 幂等语义要求把它当成“已入队”而非错误，直接返回 False。
            return False
        return True

    def claim(self, worker_id: str) -> QueueEntry | None:
        """! @brief 原子地领取优先级最高的一个可执行任务。

        原子性来源：SQLite 同一时刻只允许一个写者，且整个「查找候选 +
        更新状态 + 返回结果」封装在一条 UPDATE 语句里，中间不可能被其它
        连接插入。子查询按 priority 降序、id 升序选出候选，既处理了全新
        的 queued 任务；领取前隔离租约已到期的 claimed 任务。

        @return 领取到的队列项；没有候选时返回 None。
        """
        self.reclaim_expired()
        now = _now_iso()
        lease = _lease_expiry(now)
        with get_connection() as conn:
            row = conn.execute(
                """
                UPDATE task_queue
                SET
                    status = 'claimed',
                    claimed_by = ?,
                    lease_expires_at = ?,
                    heartbeat_at = ?,
                    attempts = attempts + 1,
                    updated_at = ?
                WHERE id = (
                    SELECT id
                    FROM task_queue
                    WHERE
                        status = 'queued' AND attempts < max_attempts
                    ORDER BY priority DESC, id
                    LIMIT 1
                )
                RETURNING *
                """,
                (
                    worker_id,
                    lease,
                    now,
                    now,
                ),
            ).fetchone()
        if row is None:
            return None
        return _row_to_entry(row)

    def heartbeat(self, worker_id: str, task_id: str) -> bool:
        """! @brief 续租：只有 claimed_by 匹配当前 Worker 时才生效。

        @return True 表示续租成功（租约与心跳时间均刷新）。
        """
        now = _now_iso()
        lease = _lease_expiry(now)
        with get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE task_queue
                SET
                    heartbeat_at = ?,
                    lease_expires_at = ?,
                    updated_at = ?
                WHERE
                    task_id = ?
                    AND claimed_by = ?
                    AND status = 'claimed'
                """,
                (now, lease, now, task_id, worker_id),
            )
            return cursor.rowcount == 1

    def complete(
        self,
        task_id: str,
        succeeded: bool,
        error: str | None = None,
        *,
        worker_id: str | None = None,
    ) -> None:
        """! @brief 任务执行结束后的队列收口。

        成功 → done；失败则看 attempts 是否已达上限：未达上限回 queued
        （清空领取字段、保留 last_error 供排查），已达上限进 dead（死信）。
        """
        now = _now_iso()
        with get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            owner = conn.execute(
                "SELECT claimed_by, status FROM task_queue WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if owner is None or owner["status"] != "claimed":
                return
            if worker_id is not None and owner["claimed_by"] != worker_id:
                return
            if succeeded:
                conn.execute(
                    """
                    UPDATE task_queue
                    SET
                        status = 'done',
                        claimed_by = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = NULL,
                        last_error = NULL,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (now, task_id),
                )
                return

            row = conn.execute(
                "SELECT attempts, max_attempts FROM task_queue WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                return
            if int(row["attempts"]) < int(row["max_attempts"]):
                conn.execute(
                    """
                    UPDATE task_queue
                    SET
                        status = 'queued',
                        claimed_by = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = NULL,
                        last_error = ?,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (error, now, task_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE task_queue
                    SET
                        status = 'dead',
                        claimed_by = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = NULL,
                        last_error = ?,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (error, now, task_id),
                )

    def reclaim_expired(self) -> int:
        """! @brief 隔离过期租约并标记中断，避免无 fencing 的自动重复执行。

        租约失效不代表旧进程已停止。必须确认旧 Worker 停止后再显式 resume；
        当前实现不承诺自动故障切换或工具副作用恰好执行一次。
        """
        now = _now_iso()
        with get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """UPDATE tasks SET status = 'interrupted', updated_at = ?
                WHERE status IN ('running', 'cancelling') AND id IN (
                    SELECT task_id FROM task_queue
                    WHERE status = 'claimed' AND lease_expires_at < ?
                )""", (now, now),
            )
            cursor = conn.execute(
                """UPDATE task_queue SET status = 'dead', claimed_by = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL, updated_at = ?,
                last_error = '租约过期：确认旧 Worker 已停止后再恢复任务'
                WHERE status = 'claimed' AND lease_expires_at < ?""", (now, now),
            )
            return cursor.rowcount

    def get(self, task_id: str) -> QueueEntry | None:
        """! @brief 按 task_id 查询队列项。"""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM task_queue WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_entry(row)

    def reset(self, task_id: str, idempotency_key: str) -> bool:
        """! @brief 把已完成/死信的队列项重置为 queued，支持任务重新执行。

        用户对 interrupted 任务显式调用 resume 端点时，task_id 的
        唯一约束会挡住新入队。reset 只回收终态（done/dead）的旧记录，
        不碰 queued/claimed，避免破坏在飞任务的幂等保护。

        @return True 表示成功重置；队列项不存在或仍在排队/执行中返回 False。
        """
        now = _now_iso()
        with get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE task_queue
                SET
                    idempotency_key = ?,
                    status = 'queued',
                    attempts = 0,
                    claimed_by = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    last_error = NULL,
                    updated_at = ?
                WHERE task_id = ? AND status IN ('done', 'dead')
                """,
                (idempotency_key, now, task_id),
            )
            return cursor.rowcount == 1

    def remove(self, task_id: str) -> bool:
        """! @brief 把任务移出队列（排队中取消场景）。

        @return True 表示存在队列项并已删除；本来就不在队列返回 False。
        """
        with get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM task_queue WHERE task_id = ? AND status = 'queued'",
                (task_id,),
            )
            return cursor.rowcount == 1

    def stats(self) -> dict[str, int]:
        """! @brief 各状态计数，缺失状态补 0。"""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM task_queue GROUP BY status"
            ).fetchall()
        result = {"queued": 0, "claimed": 0, "done": 0, "dead": 0}
        for row in rows:
            status = str(row["status"])
            if status in result:
                result[status] = int(row["n"])
        return result


class RedisTaskQueue:
    """! @brief 基于 Redis 的持久化任务队列（可选后端）。

    与 SQLiteTaskQueue 暴露同一接口，但面向多机高吞吐场景。此处为保持
    精简，用 ``ZPOPMAX`` + 哈希字段的近似的实现，生产环境应改用 Lua 脚本
    保证「弹出一条 + 更新状态」整体原子，并配合 Redis Streams/消费者组
    获得更可靠的交付语义。本实现不做单元测试，语义与局限见各方法注释。
    """

    _INDEX = "devpilot:tq:index"

    def __init__(self) -> None:
        try:
            import redis  # noqa: PLC0415  # 可选依赖，仅 redis 后端需要
        except ImportError as exc:
            raise RuntimeError("redis 依赖未安装") from exc
        if not settings.redis_url:
            raise RuntimeError("task_queue_backend=redis 但未配置 REDIS_URL")
        self._redis = redis.from_url(settings.redis_url, decode_responses=True)

    @staticmethod
    def _key(task_id: str) -> str:
        return f"devpilot:tq:{task_id}"

    @staticmethod
    def _entry_from_mapping(data: dict[str, str]) -> QueueEntry:
        """! @brief 把 Redis 哈希还原为 QueueEntry（空串视作 None）。"""
        def _s(value: str | None) -> str | None:
            return None if value in ("", None) else str(value)

        return QueueEntry(
            id=int(data.get("id") or 0),
            task_id=str(data.get("task_id") or ""),
            idempotency_key=str(data.get("idempotency_key") or ""),
            status=str(data.get("status") or ""),
            priority=int(data.get("priority") or 0),
            attempts=int(data.get("attempts") or 0),
            max_attempts=int(data.get("max_attempts") or 0),
            claimed_by=_s(data.get("claimed_by")),
            lease_expires_at=_s(data.get("lease_expires_at")),
            heartbeat_at=_s(data.get("heartbeat_at")),
            last_error=_s(data.get("last_error")),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )

    def enqueue(
        self,
        task_id: str,
        idempotency_key: str,
        priority: int = 0,
        max_attempts: int | None = None,
    ) -> bool:
        if max_attempts is None:
            max_attempts = settings.task_max_attempts
        key = self._key(task_id)
        # 先 EXISTS 再写入并非原子：极端并发下两个请求可能同时判定不存在。
        # 生产应改为 HSETNX + ZADD 的 Lua 脚本保证只入队一次。
        if self._redis.exists(key):
            return False
        now = _now_iso()
        pipe = self._redis.pipeline()
        pipe.hset(
            key,
            mapping={
                "id": "0",
                "task_id": task_id,
                "idempotency_key": idempotency_key,
                "status": "queued",
                "priority": str(priority),
                "attempts": "0",
                "max_attempts": str(max_attempts),
                "claimed_by": "",
                "lease_expires_at": "",
                "heartbeat_at": "",
                "last_error": "",
                "created_at": now,
                "updated_at": now,
            },
        )
        pipe.zadd(self._INDEX, {task_id: priority})
        pipe.execute()
        return True

    def claim(self, worker_id: str) -> QueueEntry | None:
        # ZPOPMAX 原子弹出最高优先级成员，但弹出与哈希状态更新分两步，
        # 中途崩溃会丢条目。生产应把「弹出 + 写状态 + 回填索引」打包成 Lua。
        popped = self._redis.zpopmax(self._INDEX)
        if not popped:
            return None
        task_id = popped[0][0]
        key = self._key(task_id)
        now = _now_iso()
        lease = _lease_expiry(now)
        pipe = self._redis.pipeline()
        pipe.hincrby(key, "attempts", 1)
        pipe.hset(
            key,
            mapping={
                "status": "claimed",
                "claimed_by": worker_id,
                "lease_expires_at": lease,
                "heartbeat_at": now,
                "updated_at": now,
            },
        )
        pipe.hgetall(key)
        data = pipe.execute()[-1]
        return self._entry_from_mapping(data)

    def heartbeat(self, worker_id: str, task_id: str) -> bool:
        key = self._key(task_id)
        if self._redis.hget(key, "claimed_by") != worker_id:
            return False
        if self._redis.hget(key, "status") != "claimed":
            return False
        now = _now_iso()
        self._redis.hset(
            key,
            mapping={
                "heartbeat_at": now,
                "lease_expires_at": _lease_expiry(now),
                "updated_at": now,
            },
        )
        return True

    def complete(
        self,
        task_id: str,
        succeeded: bool,
        error: str | None = None,
        *,
        worker_id: str | None = None,
    ) -> None:
        key = self._key(task_id)
        if self._redis.hget(key, "status") != "claimed":
            return
        if worker_id is not None and self._redis.hget(key, "claimed_by") != worker_id:
            return
        now = _now_iso()
        if succeeded:
            self._redis.hset(
                key,
                mapping={
                    "status": "done",
                    "claimed_by": "",
                    "lease_expires_at": "",
                    "heartbeat_at": "",
                    "last_error": "",
                    "updated_at": now,
                },
            )
            return
        attempts = int(self._redis.hget(key, "attempts") or 0)
        max_attempts = int(
            self._redis.hget(key, "max_attempts") or settings.task_max_attempts
        )
        new_status = "queued" if attempts < max_attempts else "dead"
        self._redis.hset(
            key,
            mapping={
                "status": new_status,
                "claimed_by": "",
                "lease_expires_at": "",
                "heartbeat_at": "",
                "last_error": error or "",
                "updated_at": now,
            },
        )
        if new_status == "queued":
            priority = int(self._redis.hget(key, "priority") or 0)
            self._redis.zadd(self._INDEX, {task_id: priority})

    def reclaim_expired(self) -> int:
        # 扫描全部队列键判断租约是否过期，O(N) 复杂度，仅适合小规模；
        # 生产应改用 Sorted Set 按 lease 时间维护到期集合做 O(logN) 回收。
        now = _now_iso()
        count = 0
        for key in self._redis.scan_iter(match="devpilot:tq:*"):
            if key == self._INDEX:
                continue
            if self._redis.hget(key, "status") != "claimed":
                continue
            lease = self._redis.hget(key, "lease_expires_at") or ""
            if lease and lease < now:
                task_id = key.removeprefix("devpilot:tq:")
                priority = int(self._redis.hget(key, "priority") or 0)
                self._redis.hset(
                    key,
                    mapping={
                        "status": "queued",
                        "claimed_by": "",
                        "lease_expires_at": "",
                        "heartbeat_at": "",
                        "updated_at": now,
                    },
                )
                self._redis.zadd(self._INDEX, {task_id: priority})
                count += 1
        return count

    def get(self, task_id: str) -> QueueEntry | None:
        data = self._redis.hgetall(self._key(task_id))
        if not data:
            return None
        return self._entry_from_mapping(data)

    def reset(self, task_id: str, idempotency_key: str) -> bool:
        """! @brief 回收终态（done/dead）记录并重新入队，语义同 SQLite 版。"""
        key = self._key(task_id)
        status = self._redis.hget(key, "status")
        if status not in ("done", "dead"):
            return False
        now = _now_iso()
        priority = int(self._redis.hget(key, "priority") or 0)
        self._redis.hset(
            key,
            mapping={
                "idempotency_key": idempotency_key,
                "status": "queued",
                "attempts": "0",
                "claimed_by": "",
                "lease_expires_at": "",
                "heartbeat_at": "",
                "last_error": "",
                "updated_at": now,
            },
        )
        self._redis.zadd(self._INDEX, {task_id: priority})
        return True

    def remove(self, task_id: str) -> bool:
        """! @brief 删除队列项（排队中取消场景），语义同 SQLite 版。"""
        key = self._key(task_id)
        deleted = bool(self._redis.delete(key))
        self._redis.zrem(self._INDEX, task_id)
        return deleted

    def stats(self) -> dict[str, int]:
        result = {"queued": 0, "claimed": 0, "done": 0, "dead": 0}
        for key in self._redis.scan_iter(match="devpilot:tq:*"):
            if key == self._INDEX:
                continue
            status = self._redis.hget(key, "status")
            if status in result:
                result[status] += 1
        return result


def get_task_queue() -> SQLiteTaskQueue | RedisTaskQueue:
    """! @brief 按 ``settings.task_queue_backend`` 分派队列实现。

    - ``sqlite`` → SQLiteTaskQueue；
    - ``redis`` → RedisTaskQueue；
    - ``inline`` → 抛 ValueError，因为 inline 模式在 API 进程内直接起线程，
      根本不需要（也不该）走队列。
    """
    backend = settings.task_queue_backend
    if backend == "sqlite":
        return SQLiteTaskQueue()
    if backend == "redis":
        return RedisTaskQueue()
    if backend == "inline":
        raise ValueError("inline 模式不使用任务队列")
    raise ValueError(f"未知任务队列后端: {backend}")
