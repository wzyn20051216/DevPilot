"""! @brief 仅清空已配置的 DevPilot 业务历史；默认只检查，--apply 执行。"""

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from backend.src.config import settings

HISTORY_TABLES = (
    "agent_contexts", "task_queue", "publish_previews", "task_sources", "tool_calls",
    "agent_events", "plan_steps", "evaluation_results", "tasks",
)


def reset_sqlite(path: Path, backup_dir: Path, apply: bool = False) -> dict:
    """! @brief 验证业务库、先备份，再事务清空白名单表；不修改配置和源码。"""
    if not path.is_file():
        return {"path": str(path), "exists": False, "applied": False}
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
        if not {"id", "repo_path", "question", "status"}.issubset(columns):
            raise ValueError("目标不是可识别的 DevPilot 业务数据库，拒绝清空")
        selected = [table for table in HISTORY_TABLES if table in tables]
        before = {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in selected}
        result = {"path": str(path), "before": before, "applied": False}
        if not apply:
            return result
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / "before.sqlite"
        if backup.exists():
            raise ValueError("备份已存在，拒绝覆盖；请使用新的备份目录")
        with sqlite3.connect(backup) as destination:
            connection.backup(destination)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        for table in selected:
            connection.execute(f"DELETE FROM {table}")
        if "sqlite_sequence" in tables:
            for table in selected:
                connection.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))
        connection.commit()
        after = {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in selected}
        if any(after.values()):
            raise RuntimeError("清空后仍有记录，可能存在活动写入进程")
        result.update({"after": after, "backup": str(backup), "applied": True})
        (backup_dir / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result


def main() -> None:
    """! @brief 入口只操作当前配置库，MySQL/Redis 需在停止写入后分别核对。"""
    parser = argparse.ArgumentParser(description="DevPilot SQLite 历史检查/恢复出厂")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if settings.database_backend != "sqlite":
        raise RuntimeError("当前配置不是 SQLite；请使用经过核对的 MySQL 维护流程")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    result = reset_sqlite(settings.database_path.resolve(), settings.database_path.parent / "reset_backups" / stamp, args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
