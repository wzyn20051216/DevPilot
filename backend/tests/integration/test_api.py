"""FastAPI 启动、探针和统一异常响应测试。"""

from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from backend.src.database import connection
from backend.src.main import app


def test_health_and_readiness(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """lifespan 应初始化临时数据库，并让两个健康探针成功。"""

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "devpilot.db")
    with TestClient(app) as client:
        health = client.get("/healthz")
        ready = client.get("/readyz")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert ready.status_code == 200
    assert ready.json()["checks"]["database"] is True


def test_missing_task_uses_structured_business_error(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """仓储异常应由全局处理器转换为稳定的 404 错误码。"""

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "devpilot.db")
    with TestClient(app) as client:
        response = client.get("/api/tasks/not-a-real-task")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "task_not_found"
