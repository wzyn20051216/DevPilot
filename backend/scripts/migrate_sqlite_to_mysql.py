"""! @brief 停机迁移 SQLite 到空 MySQL 库，按表验证行数，失败回滚。"""

import argparse
import json
import sqlite3
from pathlib import Path

from backend.src.config import settings
from backend.src.database.connection import begin_write, get_connection, init_database

TABLES = (
    "tasks", "plan_steps", "agent_events", "tool_calls", "task_sources",
    "publish_previews", "evaluation_results", "task_queue", "agent_contexts",
)


def migrate(source_path: Path, batch_size: int = 500) -> dict[str, int]:
    """! @brief 从只读快照复制全部业务表，目标库必须为空。

    @param source_path 已升级到当前表结构的 SQLite 库；迁移前停止 API/Worker/评测。
    @param batch_size 每批插入行数，限制内存占用。
    @return 每张表迁移后核对一致的行数。
    @exception RuntimeError 后端不正确、数据库非空或源库有在途任务。
    """

    if settings.database_backend != "mysql":
        raise RuntimeError("迁移目标必须设置 DATABASE_BACKEND=mysql 与 MYSQL_URL")
    if batch_size < 1:
        raise ValueError("batch_size 必须大于 0")
    source_path = source_path.resolve(strict=True)
    source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
    try:
        source.execute("BEGIN")
        if source.execute(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('running', 'cancelling')"
        ).fetchone()[0] or source.execute(
            "SELECT COUNT(*) FROM task_queue WHERE status = 'claimed'"
        ).fetchone()[0]:
            raise RuntimeError("源库存在在途任务，请确认旧执行者停止并处理状态后迁移")
        init_database()
        counts: dict[str, int] = {}
        with get_connection() as target:
            begin_write(target)
            for table in TABLES:
                if target.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]:
                    raise RuntimeError(f"目标表 {table} 非空，拒绝合并或覆盖")
            for table in TABLES:
                columns = [row[1] for row in source.execute(f"PRAGMA table_info({table})")]
                if not columns:
                    raise RuntimeError(f"源库缺少表 {table}，请先升级 SQLite 表结构")
                # 列名来自受信任的本地数据库元数据；引用标识符防止关键字冲突。
                names = ", ".join('`' + column.replace('`', '``') + '`' for column in columns)
                placeholders = ", ".join("?" for _ in columns)
                cursor = source.execute(f"SELECT * FROM {table}")
                expected = 0
                while rows := cursor.fetchmany(batch_size):
                    target.executemany(f"INSERT INTO {table} ({names}) VALUES ({placeholders})", rows)
                    expected += len(rows)
                actual = int(target.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
                if actual != expected:
                    raise RuntimeError(f"表 {table} 行数不一致：源 {expected}，目标 {actual}")
                counts[table] = actual
        return counts
    finally:
        source.close()


def main() -> None:
    """! @brief 命令行只输出表名与行数，不输出包含密码的连接串。"""

    parser = argparse.ArgumentParser(description="SQLite 停机迁移到空 MySQL 库")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.batch_size), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
