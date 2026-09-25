"""! @brief 数据库连接管理模块。

本模块集中管理 DevPilot 的 SQLite 数据库路径、连接创建和表初始化逻辑。
上层服务只需要调用 get_connection() 或 init_database()，不直接关心
数据库文件位置、连接参数和基础 PRAGMA 配置。
"""

import sqlite3

from ..config import settings

# 保留模块级变量，测试可以 monkeypatch 到临时数据库；默认值来自统一配置。
DATABASE_PATH = settings.database_path


def get_connection() -> sqlite3.Connection:
    """! @brief 创建一个 SQLite 数据库连接。

    每次调用都会返回新的连接对象，调用方应使用 with 语句或显式 close()
    释放连接。连接默认开启外键约束，并使用 WAL 日志模式提高并发读写体验。

    @return 配置好 row_factory 和 PRAGMA 的 sqlite3.Connection。
    """
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
    """

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
                workspace_path TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_evaluation_results_run_id
            ON evaluation_results(run_id);
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
