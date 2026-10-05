"""! @brief 恢复出厂维护仅清空业务白名单，并保护备份和无关数据。"""

import sqlite3

import pytest

from backend.scripts.reset_history import reset_sqlite


def test_reset_is_scoped_backed_up_and_idempotent(tmp_path):
    """! @brief 干跑无变更，执行有备份，保留其他表，重复执行保持空。"""
    path = tmp_path / "history.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE tasks(id TEXT, repo_path TEXT, question TEXT, status TEXT);
            INSERT INTO tasks VALUES('one','repo','task','completed');
            CREATE TABLE app_settings(name TEXT);
            INSERT INTO app_settings VALUES('keep');
        """)
    assert reset_sqlite(path, tmp_path / "dry")['before']['tasks'] == 1
    assert not (tmp_path / "dry").exists()
    result = reset_sqlite(path, tmp_path / "backup", True)
    assert result["after"]["tasks"] == 0
    with sqlite3.connect(result["backup"]) as backup:
        assert backup.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM app_settings").fetchone()[0] == "keep"
    assert reset_sqlite(path, tmp_path / "second", True)["after"]["tasks"] == 0


def test_reset_refuses_unrecognized_database(tmp_path):
    """! @brief 其他项目同名 tasks 表不符合 DevPilot 结构时拒绝删除。"""
    path = tmp_path / "other.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE tasks(id INTEGER)")
        db.execute("INSERT INTO tasks VALUES(1)")
    with pytest.raises(ValueError, match="拒绝清空"):
        reset_sqlite(path, tmp_path / "backup", True)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
