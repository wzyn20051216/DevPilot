"""! @brief 持久化任务队列（技术手册 10.2.5）。

本模块把「任务入队」与「任务执行」解耦：API 进程只负责把待执行任务写入
队列，独立的 Sandbox Worker 进程负责领取并执行。队列通过三组机制保证
在多个 Worker 并发、Worker 崩溃、重复入队等场景下的正确性：

- **租约（lease）**：领取任务时写入 ``lease_expires_at``，Worker 崩溃后
  SQLite 租约到期后隔离任务，确认旧进程停止后显式恢复；
- **心跳（heartbeat）**：存活 Worker 周期性续租，避免长任务被误判为
  崩溃而重复执行；
- **幂等（idempotency）**：入队靠 ``task_id`` 唯一约束去重；执行靠
  ``attempts`` 计数 + ``max_attempts`` 上限把反复失败的任务送进死信；
- **fencing（令牌）**：每次领取自增 ``fence_token``。旧 Worker 即使还活着，
  心跳、结算和事件写入都会被令牌校验拒绝，避免"僵尸写入"；
- **背压（backpressure）**：``queued`` 行数超过 ``task_queue_max_depth`` 时
  入队抛 ``QueueFullError``（HTTP 429）。

``SQLTaskQueue`` 同时服务 SQLite 与 MySQL（由 ``database_backend`` 决定），
SQL 差异集中在 ``database.connection`` 的方言工具里。后端可用
``settings.task_queue_backend`` 在 sqlite / redis 之间切换；默认执行方式仍为 inline。
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..config import settings
from ..database.connection import (
    IntegrityError,
    begin_write,
    for_update_skip_locked,
    get_connection,
)
from ..exceptions import QueueFullError


def _now_iso() -> str:
    """! @brief 返回微秒精度的 UTC ISO 时间字符串。

    队列的租约/心跳比较依赖时间先后。保留微秒精度能让短租约（测试与
    生产调度）的判断更精确；同一函数生成的字符串格式一致、长度固定，
    因而可以直接按字典序比较两个时间戳的先后。
    """
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _lease_score(now: str) -> float:
    """! @brief 租约到期时刻的 Unix 秒，用作租约索引的 ZSET 分数。

    Redis 的 ZSET 分数必须是数字，因此租约索引不能直接使用 ISO 字符串；
    可读的 ISO 时间仍存在队列哈希的 ``lease_expires_at`` 字段里。
    """

    expiry = datetime.fromisoformat(now) + timedelta(
        seconds=settings.task_worker_lease_seconds
    )
    return expiry.timestamp()


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
    # 每次领取单调递增；旧 Worker 的写入会被令牌校验拒绝。
    fence_token: int = 0
    created_at: str = ""
    updated_at: str = ""


def _row_to_entry(row) -> QueueEntry:
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
        fence_token=int(row["fence_token"] or 0),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


class SQLTaskQueue:
    """! @brief 基于 SQL 的持久化任务队列（SQLite / MySQL）。

    SQLite 同一时刻只有一个写者，事务内先 SELECT 再 UPDATE 已足够互斥；
    MySQL 使用 ``SELECT ... FOR UPDATE SKIP LOCKED`` 跳过被其它 Worker
    锁住的行，从而支持多 Worker 并发领取而不重复。
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
        @exception QueueFullError 排队中的任务数已达 ``task_queue_max_depth``。
        """
        if max_attempts is None:
            max_attempts = settings.task_max_attempts
        now = _now_iso()
        try:
            with get_connection() as conn:
                # 进写事务后再数排队行数：MySQL 默认 REPEATABLE READ，
                # 不开事务的读会看到旧快照，无法反映刚提交的积压。
                begin_write(conn)
                self._check_depth(conn)
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
        except IntegrityError:
            # task_id 有 UNIQUE 约束，重复入队只会触发唯一约束冲突。
            # 幂等语义要求把它当成“已入队”而非错误，直接返回 False。
            return False
        return True

    def _check_depth(self, conn) -> None:
        """! @brief 背压：排队的任务过多时拒绝新入队。

        MySQL 下这是软上限——两个并发入队可能都读到"还有一个名额"
        而后同时写入，把队列略微推过阈值。这里的目的是限制积压规模，
        不是精确配额，因此不做额外加锁。
        """
        limit = settings.task_queue_max_depth
        if limit <= 0:
            return
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM task_queue WHERE status = 'queued'"
        ).fetchone()
        depth = int(row["n"] if row is not None else 0)
        if depth >= limit:
            raise QueueFullError(
                f"任务队列已满（{depth}/{limit}），请稍后重试",
                retry_after=max(1, int(settings.task_worker_poll_seconds * 5)),
            )

    def claim(self, worker_id: str) -> QueueEntry | None:
        """! @brief 原子地领取优先级最高的一个可执行任务。

        步骤在同一个写事务内完成：选出候选 → 标记为 claimed 并自增
        ``attempts`` 与 ``fence_token`` → 读回整行。SQLite 靠"同一时刻
        只有一个写者"互斥；MySQL 用 ``FOR UPDATE SKIP LOCKED`` 跳过已被
        其它 Worker 锁住的行，因而多个 Worker 可以并发领取而不重复。

        领取前会顺带回收过期租约，让"租约丢失"和"任务被隔离"看起来是
        同一步：调用方不必记得先调 ``reclaim_expired``。回收本身只对
        具体的过期行加锁，因此不会与并发领取互相等待。

        @return 领取到的队列项；没有候选时返回 None。
        """
        self.reclaim_expired()
        now = _now_iso()
        lease = _lease_expiry(now)
        with get_connection() as conn:
            begin_write(conn)
            candidate = conn.execute(
                f"""
                SELECT id
                FROM task_queue
                WHERE status = 'queued' AND attempts < max_attempts
                ORDER BY priority DESC, id
                LIMIT 1{for_update_skip_locked()}
                """
            ).fetchone()
            if candidate is None:
                return None
            conn.execute(
                """
                UPDATE task_queue
                SET
                    status = 'claimed',
                    claimed_by = ?,
                    lease_expires_at = ?,
                    heartbeat_at = ?,
                    attempts = attempts + 1,
                    fence_token = fence_token + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (worker_id, lease, now, now, int(candidate["id"])),
            )
            row = conn.execute(
                "SELECT * FROM task_queue WHERE id = ?",
                (int(candidate["id"]),),
            ).fetchone()
        if row is None:
            return None
        return _row_to_entry(row)

    def is_current(self, task_id: str, worker_id: str, fence_token: int) -> bool:
        """! @brief 判断某次领取是否仍然持有该任务。

        执行方在写事件、写检查点或收口状态前调用本方法。令牌按任务单调
        递增，因此旧 Worker 持有的旧令牌必然与当前值不等——这正是 fencing
        要拒绝的"僵尸写入"。
        """

        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT claimed_by, status, fence_token
                FROM task_queue
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            return False
        return (
            row["status"] == "claimed"
            and row["claimed_by"] == worker_id
            and int(row["fence_token"] or 0) == fence_token
        )

    def heartbeat(
        self,
        worker_id: str,
        task_id: str,
        fence_token: int | None = None,
    ) -> bool:
        """! @brief 续租：只有当前持有者（且令牌匹配）才生效。

        @param fence_token 领取时拿到的令牌；传入后令牌不匹配即拒绝续租，
        从而让租约已过期的旧 Worker 无法把自己"续"回合法状态。
        @return True 表示续租成功（租约与心跳时间均刷新）。
        """
        now = _now_iso()
        lease = _lease_expiry(now)
        with get_connection() as conn:
            begin_write(conn)
            owner = conn.execute(
                """
                SELECT claimed_by, status, fence_token
                FROM task_queue
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if not self._owns(owner, worker_id, fence_token):
                return False
            conn.execute(
                """
                UPDATE task_queue
                SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (now, lease, now, task_id),
            )
            return True

    @staticmethod
    def _owns(owner, worker_id: str, fence_token: int | None) -> bool:
        """! @brief 判断一行队列记录是否仍由指定持有者有效占用。

        租约到期后 ``reclaim_expired`` 会把它置为 dead 并清空 ``claimed_by``，
        因此即使旧 Worker 还活着、拿着旧令牌，也会在这里被拒绝写入。
        """

        if owner is None or owner["status"] != "claimed":
            return False
        if owner["claimed_by"] != worker_id:
            return False
        if fence_token is None:
            return True
        return int(owner["fence_token"] or 0) == fence_token

    def complete(
        self,
        task_id: str,
        succeeded: bool,
        error: str | None = None,
        *,
        worker_id: str | None = None,
        fence_token: int | None = None,
    ) -> None:
        """! @brief 任务执行结束后的队列收口。

        成功 → done；失败则看 attempts 是否已达上限：未达上限回 queued
        （清空领取字段、保留 last_error 供排查），已达上限进 dead（死信）。
        持有者或令牌不匹配时直接返回，保证已被回收的任务不会被旧 Worker
        覆盖成 done。
        """
        now = _now_iso()
        with get_connection() as conn:
            begin_write(conn)
            owner = conn.execute(
                """
                SELECT claimed_by, status, fence_token
                FROM task_queue
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            # 调用方给了 worker_id 就做完整持有权校验；没给则只要求仍是 claimed，
            # 保留历史调用方式（例如 API 侧只做尽力收口）。
            if worker_id is None:
                if owner is None or owner["status"] != "claimed":
                    return
            elif not self._owns(owner, worker_id, fence_token):
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
        """! @brief 隔离过期租约并标记中断，避免自动重复执行。

        租约失效不代表旧进程已停止。必须确认旧 Worker 停止后再显式 resume；
        当前实现不承诺自动故障切换或工具副作用恰好执行一次。

        隔离动作会把 ``claimed_by`` 清空并置为 dead，因此旧 Worker 之后
        无论续租、结算还是写事件都会被 fencing 校验拒绝。

        实现上先在只读事务里查出过期任务的 ID，再按主键精确更新。这样
        锁只落在具体几行上；若像早期实现那样让 UPDATE 自己带扫描条件，
        MySQL 会对扫描范围加间隙锁，与并发 claim 的锁相互等待甚至死锁。
        """
        now = _now_iso()
        with get_connection() as conn:
            expired = [
                row["task_id"]
                for row in conn.execute(
                    """
                    SELECT task_id FROM task_queue
                    WHERE status = 'claimed' AND lease_expires_at < ?
                    """,
                    (now,),
                ).fetchall()
            ]
            if not expired:
                return 0
            marks = ", ".join("?" for _ in expired)
            begin_write(conn)
            conn.execute(
                f"""UPDATE tasks SET status = 'interrupted', updated_at = ?
                WHERE status IN ('running', 'cancelling')
                AND id IN ({marks})""",
                (now, *expired),
            )
            cursor = conn.execute(
                f"""UPDATE task_queue SET status = 'dead', claimed_by = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL, updated_at = ?,
                last_error = '租约过期：确认旧 Worker 已停止后再恢复任务'
                WHERE task_id IN ({marks})""",
                (now, *expired),
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


# 历史名称：队列实现本就同时服务 SQLite 与 MySQL，类名改成 SQLTaskQueue 后
# 保留别名，避免已有调用方和测试需要跟着改名。
SQLiteTaskQueue = SQLTaskQueue


class RedisTaskQueue:
    """! @brief 基于 Redis 的持久化任务队列（可选后端）。

    与 SQLTaskQueue 暴露同一接口，但面向多机高吞吐场景。每个操作都是一段
    Lua 脚本，在 Redis 单线程内整体执行，因此"读-判断-写"不会和其它 Worker
    交错；租约与 fencing 令牌的语义和 SQL 版保持一致。
    """

    # 可领取任务索引：score 编码「优先级 + 入队序」，成员为 task_id。
    _INDEX = "devpilot:tq:index"
    # 租约索引：score 为租约到期时间，用于 O(logN) 回收过期租约。
    _LEASE_INDEX = "devpilot:tq:leases"
    # 全局入队序号，保证同优先级按入队先后领取（对齐 SQL 版的 id ASC）。
    _SEQUENCE = "devpilot:tq:seq"

    _KEY_PREFIX = "devpilot:tq:"

    def __init__(self) -> None:
        try:
            import redis  # noqa: PLC0415  # 可选依赖，仅 redis 后端需要
        except ImportError as exc:
            raise RuntimeError("redis 依赖未安装") from exc
        if not settings.redis_url:
            raise RuntimeError("task_queue_backend=redis 但未配置 REDIS_URL")
        self._redis = redis.from_url(settings.redis_url, decode_responses=True)
        # 所有 mutating 操作都是一段 Lua：Redis 单线程执行脚本，因此
        # "读-判断-写"不会与其它 Worker 交错，也不会在中途崩溃时丢条目。
        # 索引键以 KEYS 传入而非在脚本里拼接，兼容 Redis Cluster 的键槽约束。
        self._lua_enqueue = self._redis.register_script(_LUA_ENQUEUE)
        self._lua_claim = self._redis.register_script(_LUA_CLAIM)
        self._lua_heartbeat = self._redis.register_script(_LUA_HEARTBEAT)
        self._lua_complete = self._redis.register_script(_LUA_COMPLETE)
        self._lua_reclaim = self._redis.register_script(_LUA_RECLAIM)
        self._lua_reset = self._redis.register_script(_LUA_RESET)

    @staticmethod
    def _key(task_id: str) -> str:
        return f"devpilot:tq:{task_id}"

    @staticmethod
    def _entry_from_mapping(data: dict[str, str] | list[str]) -> QueueEntry:
        """! @brief 把 Redis 哈希还原为 QueueEntry（空串视作 None）。

        Lua 里的 ``HGETALL`` 返回扁平数组（``{字段, 值, 字段, 值}``），
        而 redis-py 的 ``hgetall`` 直接返回字典；两种形态都接受。
        """

        if isinstance(data, list):
            data = dict(zip(data[0::2], data[1::2], strict=False))

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
            fence_token=int(data.get("fence_token") or 0),
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
        """! @brief 原子入队（幂等 + 深度检查），语义同 SQL 版。"""

        if max_attempts is None:
            max_attempts = settings.task_max_attempts
        result = self._lua_enqueue(
            keys=[self._key(task_id), self._INDEX, self._SEQUENCE],
            args=[
                task_id,
                idempotency_key,
                priority,
                max_attempts,
                _now_iso(),
                settings.task_queue_max_depth,
            ],
        )
        if result == "full":
            raise QueueFullError(
                f"任务队列已满（上限 {settings.task_queue_max_depth}），请稍后重试",
                retry_after=max(1, int(settings.task_worker_poll_seconds * 5)),
            )
        return bool(result)

    def claim(self, worker_id: str) -> QueueEntry | None:
        """! @brief 原子领取最高优先级任务，语义同 SQL 版。"""

        now = _now_iso()
        data = self._lua_claim(
            keys=[self._INDEX, self._LEASE_INDEX],
            args=[
                worker_id,
                now,
                _lease_expiry(now),
                _lease_score(now),
                self._KEY_PREFIX,
            ],
        )
        if not data:
            return None
        return self._entry_from_mapping(data)

    def heartbeat(
        self,
        worker_id: str,
        task_id: str,
        fence_token: int | None = None,
    ) -> bool:
        """! @brief 续租：令牌或持有者不匹配时拒绝，语义同 SQL 版。"""

        now = _now_iso()
        token = -1 if fence_token is None else fence_token
        result = self._lua_heartbeat(
            keys=[self._key(task_id), self._LEASE_INDEX],
            args=[worker_id, now, _lease_expiry(now), _lease_score(now), token],
        )
        return bool(result)

    def complete(
        self,
        task_id: str,
        succeeded: bool,
        error: str | None = None,
        *,
        worker_id: str | None = None,
        fence_token: int | None = None,
    ) -> None:
        """! @brief 队列收口，语义同 SQL 版。"""

        token = -1 if fence_token is None else fence_token
        self._lua_complete(
            keys=[
                self._key(task_id),
                self._INDEX,
                self._LEASE_INDEX,
                self._SEQUENCE,
            ],
            args=[
                worker_id or "",
                1 if succeeded else 0,
                error or "",
                _now_iso(),
                token,
                settings.task_queue_max_depth,
            ],
        )

    def reclaim_expired(self) -> int:
        """! @brief 隔离过期租约，语义同 SQL 版。

        租约索引按到期时间排序，因此只需取到期集合而无需全库扫描。
        隔离结果与 SQL 版一致：置为 dead、清空持有者，等待人工确认后恢复。
        """

        now = _now_iso()
        result = self._lua_reclaim(
            keys=[self._LEASE_INDEX, self._INDEX],
            args=[now, _lease_score(now), self._KEY_PREFIX],
        )
        return int(result or 0)

    def get(self, task_id: str) -> QueueEntry | None:
        """! @brief 按 task_id 查询队列项。"""

        data = self._redis.hgetall(self._key(task_id))
        if not data:
            return None
        return self._entry_from_mapping(data)

    def is_current(self, task_id: str, worker_id: str, fence_token: int) -> bool:
        """! @brief 判断某次领取是否仍持有该任务，语义同 SQL 版。"""

        holder, status, token = self._redis.hmget(
            self._key(task_id),
            "claimed_by",
            "status",
            "fence_token",
        )
        return (
            holder == worker_id
            and status == "claimed"
            and int(token or 0) == fence_token
        )

    def reset(self, task_id: str, idempotency_key: str) -> bool:
        """! @brief 回收终态（done/dead）记录并重新入队，语义同 SQL 版。"""

        result = self._lua_reset(
            keys=[self._key(task_id), self._INDEX, self._SEQUENCE],
            args=[task_id, idempotency_key, _now_iso()],
        )
        return bool(result)

    def remove(self, task_id: str) -> bool:
        """! @brief 删除队列项（排队中取消场景），语义同 SQL 版。

        同时清理可领取索引与租约索引，避免残留条目在后续 ``claim`` 里
        被反复取出又丢弃。只应在任务仍在排队时调用。
        """

        pipe = self._redis.pipeline()
        pipe.delete(self._key(task_id))
        pipe.zrem(self._INDEX, task_id)
        pipe.zrem(self._LEASE_INDEX, task_id)
        return bool(pipe.execute()[0])

    def stats(self) -> dict[str, int]:
        """! @brief 各状态计数，语义同 SQL 版。

        只遍历索引 ZSET 的成员，不做全库 ``SCAN``；索引只保留 queued 任务，
        其余状态直接查哈希，避免脏数据让统计整体失败。
        """

        result = {"queued": 0, "claimed": 0, "done": 0, "dead": 0}
        for task_id in self._redis.zrange(self._INDEX, 0, -1):
            status = self._redis.hget(self._key(task_id), "status")
            if status in result:
                result[status] += 1
        return result


# --- Redis Lua 脚本 -------------------------------------------------------
# 约定：哈希键由脚本用前缀拼出（KEYS 只带索引键），以兼容 Redis Cluster 的
# 键槽约束——带前缀的键会落在同一槽，索引键则显式作为 KEYS 传入。
# 分数编码 (priority + 1) * 10^10 + 入队序号：先比优先级，再比入队先后，
# 与 SQL 版的 ``ORDER BY priority DESC, id`` 一致。
_LUA_ENQUEUE = """
local key, index, seq = KEYS[1], KEYS[2], KEYS[3]
local task_id, idem, priority, max_attempts, now, max_depth =
    ARGV[1], ARGV[2], tonumber(ARGV[3]), tonumber(ARGV[4]), ARGV[5],
    tonumber(ARGV[6])
if redis.call('EXISTS', key) == 1 then
    return 0
end
if max_depth and max_depth > 0 and redis.call('ZCARD', index) >= max_depth then
    return 'full'
end
local seq_no = redis.call('INCR', seq)
redis.call('HSET', key,
    'id', '0',
    'task_id', task_id,
    'idempotency_key', idem,
    'status', 'queued',
    'priority', priority,
    'attempts', '0',
    'max_attempts', max_attempts,
    'claimed_by', '',
    'lease_expires_at', '',
    'heartbeat_at', '',
    'last_error', '',
    'fence_token', '0',
    'created_at', now,
    'updated_at', now)
redis.call('ZADD', index, (priority + 1) * 10000000000 + seq_no, task_id)
return 1
"""

_LUA_CLAIM = """
local index, leases = KEYS[1], KEYS[2]
local worker, now, lease, lease_score, prefix =
    ARGV[1], ARGV[2], ARGV[3], ARGV[4], ARGV[5]
for _ = 1, 64 do
    local cand = redis.call('ZRANGE', index, 0, 0)
    if #cand == 0 then
        return nil
    end
    local task_id = cand[1]
    local key = prefix .. task_id
    local status = redis.call('HGET', key, 'status')
    local attempts = tonumber(redis.call('HGET', key, 'attempts') or '0')
    local max_attempts = tonumber(redis.call('HGET', key, 'max_attempts') or '0')
    if status ~= 'queued' or attempts >= max_attempts or max_attempts == 0 then
        -- 索引里的陈旧条目（已领取/已结束/超出尝试上限）直接摘掉，
        -- 否则它会一直卡在队首阻塞后面的任务。
        redis.call('ZREM', index, task_id)
    else
        redis.call('ZREM', index, task_id)
        redis.call('HSET', key,
            'status', 'claimed',
            'claimed_by', worker,
            'lease_expires_at', lease,
            'heartbeat_at', now,
            'fence_token', tonumber(redis.call('HGET', key, 'fence_token') or '0') + 1,
            'updated_at', now)
        redis.call('HINCRBY', key, 'attempts', 1)
        redis.call('ZADD', leases, lease_score, task_id)
        return redis.call('HGETALL', key)
    end
end
return nil
"""

_LUA_HEARTBEAT = """
local key, leases = KEYS[1], KEYS[2]
local worker, now, lease, lease_score, token =
    ARGV[1], ARGV[2], ARGV[3], ARGV[4], tonumber(ARGV[5])
if redis.call('HGET', key, 'status') ~= 'claimed' then
    return 0
end
if redis.call('HGET', key, 'claimed_by') ~= worker then
    return 0
end
if token >= 0 and tonumber(redis.call('HGET', key, 'fence_token') or '0') ~= token then
    return 0
end
redis.call('HSET', key, 'heartbeat_at', now, 'lease_expires_at', lease,
    'updated_at', now)
-- 成员就是 task_id，重写分数即完成续租，无需先删旧成员。
redis.call('ZADD', leases, lease_score, redis.call('HGET', key, 'task_id'))
return 1
"""

_LUA_COMPLETE = """
local key, index, leases, seq = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
local worker, succeeded, err, now, token, max_depth =
    ARGV[1], tonumber(ARGV[2]), ARGV[3], ARGV[4], tonumber(ARGV[5]),
    tonumber(ARGV[6])
local status = redis.call('HGET', key, 'status')
if status ~= 'claimed' then
    return 0
end
if worker ~= '' then
    if redis.call('HGET', key, 'claimed_by') ~= worker then
        return 0
    end
    if token >= 0 and tonumber(redis.call('HGET', key, 'fence_token') or '0') ~= token then
        return 0
    end
end
local task_id = redis.call('HGET', key, 'task_id')
redis.call('ZREM', leases, task_id)
if succeeded == 1 then
    redis.call('HSET', key, 'status', 'done', 'claimed_by', '',
        'lease_expires_at', '', 'heartbeat_at', '', 'last_error', '',
        'updated_at', now)
    return 1
end
local attempts = tonumber(redis.call('HGET', key, 'attempts') or '0')
local max_attempts = tonumber(redis.call('HGET', key, 'max_attempts') or '0')
local new_status = 'dead'
if attempts < max_attempts then
    local depth = redis.call('ZCARD', index)
    if not (max_depth and max_depth > 0 and depth >= max_depth) then
        new_status = 'queued'
    end
end
redis.call('HSET', key, 'status', new_status, 'claimed_by', '',
    'lease_expires_at', '', 'heartbeat_at', '', 'last_error', err,
    'updated_at', now)
if new_status == 'queued' then
    local priority = tonumber(redis.call('HGET', key, 'priority') or '0')
    local seq_no = redis.call('INCR', seq)
    redis.call('ZADD', index, (priority + 1) * 10000000000 + seq_no, task_id)
end
return 1
"""

_LUA_RECLAIM = """
local leases, index = KEYS[1], KEYS[2]
local now, now_score, prefix = ARGV[1], ARGV[2], ARGV[3]
local expired = redis.call('ZRANGEBYSCORE', leases, '-inf', now_score)
local count = 0
for _, task_id in ipairs(expired) do
    local key = prefix .. task_id
    if redis.call('HGET', key, 'status') == 'claimed' then
        -- 与 SQL 版一致：隔离为死信而不是自动重新入队，因为租约失效
        -- 并不证明旧 Worker 已经停止，需要人工确认后再 resume。
        redis.call('HSET', key, 'status', 'dead', 'claimed_by', '',
            'lease_expires_at', '', 'heartbeat_at', '',
            'last_error', '租约过期：确认旧 Worker 已停止后再恢复任务',
            'updated_at', now)
        count = count + 1
    end
    redis.call('ZREM', leases, task_id)
end
return count
"""

_LUA_RESET = """
local key, index, seq = KEYS[1], KEYS[2], KEYS[3]
local task_id, idem, now = ARGV[1], ARGV[2], ARGV[3]
local status = redis.call('HGET', key, 'status')
if status ~= 'done' and status ~= 'dead' then
    return 0
end
local priority = tonumber(redis.call('HGET', key, 'priority') or '0')
redis.call('HSET', key, 'idempotency_key', idem, 'status', 'queued',
    'attempts', '0', 'claimed_by', '', 'lease_expires_at', '',
    'heartbeat_at', '', 'last_error', '', 'updated_at', now)
local seq_no = redis.call('INCR', seq)
redis.call('ZADD', index, (priority + 1) * 10000000000 + seq_no, task_id)
return 1
"""


def get_task_queue() -> SQLTaskQueue | RedisTaskQueue:
    """! @brief 按 ``settings.task_queue_backend`` 分派队列实现。

    - ``sqlite`` → SQLTaskQueue，底层跟随 ``database_backend``（SQLite 或 MySQL）；
    - ``redis`` → RedisTaskQueue；
    - ``inline`` → 抛 ValueError，因为 inline 模式在 API 进程内直接起线程，
      根本不需要（也不该）走队列。
    """
    backend = settings.task_queue_backend
    if backend == "sqlite":
        return SQLTaskQueue()
    if backend == "redis":
        return RedisTaskQueue()
    if backend == "inline":
        raise ValueError("inline 模式不使用任务队列")
    raise ValueError(f"未知任务队列后端: {backend}")
