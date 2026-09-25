"""FastAPI 启动、探针和统一异常响应测试。"""

from collections.abc import Iterator
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from backend.src import main
from backend.src.database import connection
from backend.src.database.task_repository import task_repository
from backend.src.main import app
from backend.src.models.agent_state import AgentEvent, PlanStep


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


def test_execute_failure_is_streamed_and_persisted(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """执行中断时 SSE、任务状态和事件历史必须同时记录失败。"""

    class FailingOrchestrator:
        def __init__(self, repo_path: str) -> None:
            self.repo_path = repo_path

        def execute_stream(
            self,
            question: str,
            plan: list[PlanStep],
        ) -> Iterator[AgentEvent]:
            yield AgentEvent(type="start", agent="coder", message="started")
            raise RuntimeError("pipeline crashed")

    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "devpilot.db")
    monkeypatch.setattr(main, "DevPilotOrchestrator", FailingOrchestrator)

    with TestClient(app) as client:
        task = task_repository.create_task(
            repo_path=str(tmp_path),
            question="test",
            plan=[PlanStep(id=1, title="step", description="test")],
        )
        response = client.post(f"/api/tasks/{task.id}/execute")
        detail = client.get(f"/api/tasks/{task.id}")

    assert response.status_code == 200
    assert "pipeline crashed" in response.text
    payload = detail.json()
    assert payload["task"]["status"] == "failed"
    assert [event["type"] for event in payload["events"]] == ["start", "error"]
