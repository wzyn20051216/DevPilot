"""! @brief 数据库连接管理模块。

本模块集中管理 DevPilot 的 SQLite 数据库路径、连接创建和表初始化逻辑。
上层服务只需要调用 get_connection() 或 init_database()，不直接关心
数据库文件位置、连接参数和基础 PRAGMA 配置。
"""

import sqlite3
import threading
from collections.abc import Iterable, Sequence
from contextlib import suppress
from typing import Any
from urllib.parse import unquote, urlparse

from ..config import settings

try:  # PyMySQL 只在 database_backend=mysql 时需要
    import pymysql
    from pymysql.constants import CLIENT
    from pymysql.cursors import DictCursor

    _MYSQL_INTEGRITY_ERRORS: tuple[type[BaseException], ...] = (pymysql.err.IntegrityError,)
except ImportError:  # pragma: no cover - 仅 sqlite 部署不要求安装
    pymysql = None
    CLIENT = None
    DictCursor = None
    _MYSQL_INTEGRITY_ERRORS = ()

# 保留模块级变量，测试可以 monkeypatch 到临时数据库；默认值来自统一配置。
DATABASE_PATH = settings.database_path

# 唯一约束冲突在两个后端的异常类型不同，调用方统一捕获该元组。
IntegrityError: tuple[type[BaseException], ...] = (
    sqlite3.IntegrityError,
    *_MYSQL_INTEGRITY_ERRORS,
)


def is_mysql() -> bool:
    """! @brief 当前是否使用 MySQL 作为业务状态存储。"""

    return settings.database_backend == "mysql"


def translate_placeholders(sql: str) -> str:
    """! @brief 把仓储层统一使用的 ``?`` 占位符转换为 PyMySQL 的 ``%s``。

    仓储层 SQL 里不含字面量 ``?``；字面量 ``%`` 先转义，避免被当作格式符。
    """

    return sql.replace("%", "%%").replace("?", "%s")


def begin_write(conn: "sqlite3.Connection | MySQLConnection") -> None:
    """! @brief 显式开启写事务：SQLite 立即获取写锁，MySQL 开启事务。"""

    if isinstance(conn, sqlite3.Connection):
        conn.execute("BEGIN IMMEDIATE")
    else:
        conn.begin()


def for_update_skip_locked() -> str:
    """! @brief 行级领取锁后缀；SQLite 单写者天然互斥，因此为空串。"""

    return " FOR UPDATE SKIP LOCKED" if is_mysql() else ""


def upsert_sql(
    table: str,
    key_columns: Sequence[str],
    columns: Sequence[str],
) -> str:
    """! @brief 生成"存在则更新"的写入语句，两个后端语义一致。

    表名与列名只来自代码常量，不接收外部输入，因此直接拼接是安全的。
    """

    names = ", ".join(columns)
    marks = ", ".join("?" for _ in columns)
    updates = [column for column in columns if column not in key_columns]
    if not updates:
        raise ValueError("upsert 至少需要一个非主键列")
    if is_mysql():
        assignments = ", ".join(f"{column} = VALUES({column})" for column in updates)
        return (
            f"INSERT INTO {table} ({names}) VALUES ({marks}) "
            f"ON DUPLICATE KEY UPDATE {assignments}"
        )
    assignments = ", ".join(f"{column} = excluded.{column}" for column in updates)
    keys = ", ".join(key_columns)
    return (
        f"INSERT INTO {table} ({names}) VALUES ({marks}) "
        f"ON CONFLICT({keys}) DO UPDATE SET {assignments}"
    )


class MySQLConnection:
    """! @brief 让 PyMySQL 连接兼容仓储层使用的 sqlite3 子集。

    仓储层只用到 ``with get_connection() as conn``、``conn.execute()``、
    ``executemany``、游标的 ``fetchone/fetchall/rowcount``，以及按列名取值。
    本包装补齐这些行为：退出 ``with`` 时提交或回滚，并把连接归还连接池。
    """

    def __init__(self, raw: Any, pool: "_MySQLPool") -> None:
        self._raw = raw
        self._pool = pool
        self._released = False

    def execute(self, sql: str, params: Iterable[Any] = ()) -> Any:
        cursor = self._raw.cursor()
        cursor.execute(translate_placeholders(sql), tuple(params))
        return cursor

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]) -> Any:
        cursor = self._raw.cursor()
        cursor.executemany(
            translate_placeholders(sql),
            [tuple(params) for params in seq_of_params],
        )
        return cursor

    def begin(self) -> None:
        self._raw.begin()

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        if self._released:
            return
        self._released = True
        self._pool.release(self._raw)

    def __enter__(self) -> "MySQLConnection":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        try:
            if exc_type is None:
                self._raw.commit()
            else:
                self._raw.rollback()
        finally:
            self.close()
        return False


def _connect_mysql() -> Any:
    """! @brief 按 ``mysql_url`` 建立一条新的 PyMySQL 连接。

    ``FOUND_ROWS`` 让 ``rowcount`` 表示匹配行数而非变更行数，与 SQLite 一致；
    否则"状态没变化的 UPDATE"会被误判为目标不存在。
    """

    if pymysql is None:
        raise RuntimeError("database_backend=mysql 需要安装 pymysql（uv sync --extra mysql）")
    url = urlparse(settings.mysql_url.get_secret_value())
    if url.scheme not in {"mysql", "mysql+pymysql"} or not url.hostname:
        raise RuntimeError("MYSQL_URL 格式应为 mysql://user:password@host:3306/database")
    return pymysql.connect(
        host=url.hostname,
        port=url.port or 3306,
        user=unquote(url.username or ""),
        password=unquote(url.password or ""),
        database=url.path.lstrip("/"),
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=10,
        client_flag=CLIENT.FOUND_ROWS,
    )


class _MySQLPool:
    """! @brief 软连接池：空闲连接复用，不阻塞；超出容量的连接归还时直接关闭。"""

    def __init__(self) -> None:
        self._idle: list[Any] = []
        self._lock = threading.Lock()

    def acquire(self) -> Any:
        while True:
            with self._lock:
                raw = self._idle.pop() if self._idle else None
            if raw is None:
                return _connect_mysql()
            try:
                raw.ping(reconnect=True)
                return raw
            except Exception:  # noqa: BLE001 - 失效连接直接丢弃并重试
                with suppress(Exception):
                    raw.close()

    def release(self, raw: Any) -> None:
        with suppress(Exception):
            raw.rollback()
        with self._lock:
            if len(self._idle) < settings.mysql_pool_size:
                self._idle.append(raw)
                return
        with suppress(Exception):
            raw.close()


_MYSQL_POOL = _MySQLPool()


def get_connection() -> "sqlite3.Connection | MySQLConnection":
    """! @brief 创建一个数据库连接（SQLite 或 MySQL，由 ``database_backend`` 决定）。

    每次调用都会返回新的连接对象，调用方应使用 with 语句或显式 close()
    释放连接。SQLite 连接默认开启外键约束，并使用 WAL 日志模式提高并发读写体验；
    MySQL 连接来自进程内连接池，退出 with 时提交并归还。

    @return 配置好 row_factory 和 PRAGMA 的 sqlite3.Connection，或 MySQLConnection。
    """
    if is_mysql():
        return MySQLConnection(_MYSQL_POOL.acquire(), _MYSQL_POOL)
    DATABASE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    connection = sqlite3.connect(
        database=DATABASE_PATH,
        timeout=30,
    )
    # 默认查询结果是位置元组；Row 允许同时使用 row[0] 和 row["status"]，
    # Repository 因而可以按字段名组装 Pydantic 模型，可读性更高。
    connection.row_factory = sqlite3.Row
    # SQLite 的外键约束是“每条连接单独启用”，不能只在建表时设置一次。
    connection.execute(
        "PRAGMA foreign_keys = ON"
    )
    # WAL 允许读取者与写入者更多地并行；timeout=30 则给短暂写锁留出等待时间。
    connection.execute(
        "PRAGMA journal_mode = WAL"
    )
    return connection


def init_database() -> None:
    """! @brief 初始化 DevPilot 运行所需的数据表。

    当前包括：
    - tasks：任务主表；
    - plan_steps：Planner 生成的计划步骤；
    - agent_events：Agent SSE 事件轨迹；
    - tool_calls：工具调用轨迹；
    - task_sources：任务来源，例如 GitHub Issue 导入记录；
    - publish_previews：发布 PR 前给用户审批的预览草稿。
    - evaluation_results：第十四关 Evals 的逐次实验结果。
    - task_queue：API/Worker 分离模式的持久化任务队列（租约+心跳+幂等）。
    - agent_contexts：上下文断点恢复的 Agent 消息检查点。
    """

    if is_mysql():
        # MySQL 是全新库，建表语句已含全部迁移列，不需要 PRAGMA 式的增量迁移。
        from .mysql_schema import MYSQL_STATEMENTS

        with get_connection() as conn:
            for statement in MYSQL_STATEMENTS:
                conn.execute(statement)
        return

    with get_connection() as conn:
        # executescript 一次提交整组幂等 DDL。IF NOT EXISTS 让应用每次启动
        # 都能安全调用初始化，而不会覆盖已有任务和追踪记录。
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                repo_path TEXT NOT NULL,
                question TEXT NOT NULL,
                status TEXT NOT NULL,
                execution_mode TEXT NOT NULL DEFAULT 'single_no_rag',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS plan_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT NOT NULL,
                FOREIGN KEY(task_id)
                    REFERENCES tasks(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS agent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                agent TEXT NOT NULL,
                iteration INTEGER NOT NULL,
                message TEXT NOT NULL,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id)
                    REFERENCES tasks(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tool_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                iteration INTEGER NOT NULL,
                tool TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                result_preview TEXT NOT NULL,
                duration_seconds REAL NOT NULL DEFAULT 0,
                succeeded INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id)
                    REFERENCES tasks(id)
                    ON DELETE CASCADE
            );

            -- 记录任务来源。普通用户手动创建的任务可以没有 source；
            -- GitHub Issue 导入的任务会在这里保存 owner/repo/issue/url 等信息。
            CREATE TABLE IF NOT EXISTS task_sources (
                task_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_json TEXT NOT NULL,
                FOREIGN KEY(task_id)
                    REFERENCES tasks(id)
                    ON DELETE CASCADE
            );

            -- 发布前预览表。真正推送 GitHub 前，先把文件列表、
            -- 目标分支、PR 草稿、快照 hash 和审批状态保存下来。
            CREATE TABLE IF NOT EXISTS publish_previews (
                task_id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                repo TEXT NOT NULL,
                base_branch TEXT NOT NULL,
                head_branch TEXT NOT NULL,
                commit_message TEXT NOT NULL,
                pr_title TEXT NOT NULL,
                pr_body TEXT NOT NULL,
                files_json TEXT NOT NULL,
                snapshot_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                pr_url TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(task_id)
                    REFERENCES tasks(id)
                    ON DELETE CASCADE
            );

            -- 每个 case + variant 保存一行原始实验结果。聚合指标应在查询时
            -- 计算，避免后续增加样本后还要维护另一份容易过期的汇总数据。
            CREATE TABLE IF NOT EXISTS evaluation_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                variant TEXT NOT NULL,
                repeat_index INTEGER NOT NULL DEFAULT 1,
                success INTEGER NOT NULL,
                tests_passed INTEGER NOT NULL,
                tool_calls INTEGER NOT NULL,
                iterations INTEGER NOT NULL,
                repair_rounds INTEGER NOT NULL,
                elapsed_seconds REAL NOT NULL,
                prompt_tokens INTEGER NOT NULL,
                completion_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                llm_seconds REAL NOT NULL DEFAULT 0,
                tool_seconds REAL NOT NULL DEFAULT 0,
                estimated_cost REAL NOT NULL DEFAULT 0,
                workspace_path TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL
            );

            -- 10.2.5 API/Sandbox Worker 分离：持久化任务队列。
            -- task_id 唯一约束 + idempotency_key 保证同一任务不会被重复入队；
            -- 租约（lease_expires_at）+ 心跳（heartbeat_at）防止多 Worker
            -- 重复执行或 Worker 崩溃后任务被永久占用。
            CREATE TABLE IF NOT EXISTS task_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL UNIQUE,
                idempotency_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                priority INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                claimed_by TEXT,
                lease_expires_at TEXT,
                heartbeat_at TEXT,
                last_error TEXT,
                fence_token INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(task_id)
                    REFERENCES tasks(id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_task_queue_status
            ON task_queue(status, priority, id);

            -- 10.1 上下文断点恢复：按 (task_id, agent) 保存最新消息检查点，
            -- 服务重启后 resume 可以从检查点恢复模型上下文而不是重跑 Agent。
            CREATE TABLE IF NOT EXISTS agent_contexts (
                task_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                messages_json TEXT NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                truncated INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (task_id, agent_name),
                FOREIGN KEY(task_id)
                    REFERENCES tasks(id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_evaluation_results_run_id
            ON evaluation_results(run_id);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_events_task_sequence
            ON agent_events(task_id, sequence);
            """
        )

        # 第十四关早期版本没有 tests_passed，已有数据库不能仅靠
        # CREATE TABLE IF NOT EXISTS 自动升级。这里做一次幂等轻量迁移，
        # 并用旧 success 回填历史四组实验，保留用户已经消耗额度跑出的数据。
        evaluation_columns = {
            str(row["name"])
            for row in conn.execute(
                "PRAGMA table_info(evaluation_results)"
            ).fetchall()
        }
        if "tests_passed" not in evaluation_columns:
            conn.execute(
                """
                ALTER TABLE evaluation_results
                ADD COLUMN tests_passed INTEGER NOT NULL DEFAULT 0
                """
            )
            conn.execute(
                """
                UPDATE evaluation_results
                SET tests_passed = success
                """
            )
        if "repeat_index" not in evaluation_columns:
            # 历史数据没有显式重复序号，按旧行为统一视为第 1 次运行。
            conn.execute(
                """
                ALTER TABLE evaluation_results
                ADD COLUMN repeat_index INTEGER NOT NULL DEFAULT 1
                """
            )
        for column in ("llm_seconds", "tool_seconds", "estimated_cost"):
            if column not in evaluation_columns:
                conn.execute(
                    f"ALTER TABLE evaluation_results ADD COLUMN {column} REAL NOT NULL DEFAULT 0"
                )
        tool_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(tool_calls)").fetchall()
        }
        if "duration_seconds" not in tool_columns:
            conn.execute(
                "ALTER TABLE tool_calls ADD COLUMN duration_seconds REAL NOT NULL DEFAULT 0"
            )
        if "succeeded" not in tool_columns:
            conn.execute(
                "ALTER TABLE tool_calls ADD COLUMN succeeded INTEGER NOT NULL DEFAULT 1"
            )
        queue_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(task_queue)").fetchall()
        }
        if "fence_token" not in queue_columns:
            # fencing 令牌每次领取单调递增；存量队列行从 0 开始即可。
            conn.execute(
                "ALTER TABLE task_queue ADD COLUMN fence_token INTEGER NOT NULL DEFAULT 0"
            )
        task_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        if "execution_mode" not in task_columns:
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'single_no_rag'"
            )
