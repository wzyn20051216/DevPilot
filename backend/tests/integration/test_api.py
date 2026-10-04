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
        def __init__(self, repo_path: str, cancel_check=None, **_kwargs) -> None:
            self.repo_path = repo_path
            self.cancel_check = cancel_check

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
            execution_mode="multi_no_rag",
        )
        response = client.post(f"/api/tasks/{task.id}/execute")
        detail = client.get(f"/api/tasks/{task.id}")

    assert response.status_code == 200
    assert "pipeline crashed" in response.text
    payload = detail.json()
    assert payload["task"]["status"] == "failed"
    assert [event["type"] for event in payload["events"]] == ["start", "error"]


def test_queue_api_restart_preserves_worker_and_rejects_invalid_commands(tmp_path, monkeypatch):
    """! @brief API 重启不打断独立 Worker，队列端点遵守任务状态约束。"""
    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "queue.db")
    monkeypatch.setattr(main.settings, "task_queue_backend", "sqlite")
    connection.init_database()
    task = task_repository.create_task(repo_path=str(tmp_path), question="fix", plan=[])
    task_repository.claim_status(task.id, {"awaiting_approval"}, "running")
    with TestClient(app) as client:
        assert task_repository.get_task(task.id).status == "running"
        assert client.post(f"/api/tasks/{task.id}/execute").status_code == 409
        assert client.post(f"/api/tasks/{task.id}/resume").status_code == 409
        task_repository.claim_status(task.id, {"running"}, "completed")
        assert client.post(f"/api/tasks/{task.id}/execute").status_code == 409


def test_queued_resume_stream_waits_for_worker(monkeypatch):
    """! @brief interrupted 已入队时 SSE 不能在 Worker 领取前提前结束。"""
    import asyncio
    from types import SimpleNamespace
    from backend.src.services import task_queue

    events = iter([[], [{"sequence": 1, "type": "final", "agent": "coder", "iteration": 1,
                        "message": "done", "data": {}, "created_at": "now"}], []])
    statuses = iter(["interrupted", "completed", "completed"])
    monkeypatch.setattr(main.settings, "task_queue_backend", "sqlite")
    monkeypatch.setattr(main, "task_repository", SimpleNamespace(
        get_events_after=lambda *_: next(events),
        get_task=lambda *_: SimpleNamespace(status=next(statuses)),
    ))
    monkeypatch.setattr(task_queue, "get_task_queue", lambda: SimpleNamespace(
        get=lambda _: SimpleNamespace(status="queued"),
    ))
    monkeypatch.setattr(main.time, "sleep", lambda _: None)

    async def consume():
        return [item async for item in main._task_event_stream("task", 0).body_iterator]

    assert "done" in "".join(asyncio.run(consume()))


def test_inline_backpressure_keeps_status_and_returns_retry_after(tmp_path, monkeypatch):
    """! @brief execute/resume 超限均返回 429 与 Retry-After，允许重试。"""
    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "capacity.db")
    monkeypatch.setattr(main.settings, "task_inline_max_running", 1)
    monkeypatch.setattr(main.task_execution_service, "running_count", lambda: 1)
    with TestClient(app) as client:
        for status, command in [("awaiting_approval", "execute"), ("interrupted", "resume")]:
            task = task_repository.create_task(repo_path=str(tmp_path), question="fix", plan=[])
            if status == "interrupted":
                task_repository.claim_status(task.id, {"awaiting_approval"}, status)
            response = client.post(f"/api/tasks/{task.id}/{command}")
            assert response.status_code == 429
            assert int(response.headers["Retry-After"]) > 0
            assert task_repository.get_task(task.id).status == status


def test_api_key_authentication_health_exemptions_and_cors(tmp_path, monkeypatch):
    """! @brief API Key 覆盖读写/SSE；探针与 CORS 预检保持可达。"""
    from pydantic import SecretStr
    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(main.settings, "api_keys", SecretStr("first-key, second-key"))
    with TestClient(app) as client:
        for url, method in [("/api/evals/summary", "get"),
                            ("/api/tasks/missing/execute", "post"),
                            ("/api/tasks/missing/events", "get")]:
            response = getattr(client, method)(url)
            assert response.status_code == 401
            assert response.headers["WWW-Authenticate"] == "Bearer"
            assert response.json()["error"]["code"] == "unauthenticated"
        for headers in [{"Authorization": "Bearer first-key"},
                        {"X-API-Key": "second-key"},
                        {"Authorization": "bearer second-key"}]:
            assert client.get("/api/auth/check", headers=headers).status_code == 200
        for headers in [{"Authorization": "Basic first-key"},
                        {"Authorization": "Bearer invalid"},
                        {"X-API-Key": "invalid"}]:
            assert client.get("/api/auth/check", headers=headers).status_code == 401
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200
        preflight = client.options("/api/tasks/plan", headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization",
        })
        assert preflight.status_code == 200


def test_repo_whitelist_rejects_traversal_and_all_request_models(tmp_path, monkeypatch):
    """! @brief 所有仓库入口均先授权；规范化路径避免目录前缀误匹配。"""
    import pytest
    from pydantic import ValidationError
    from backend.src.schemas import AgentRunRequest, GitHubIssueImportRequest, RepositoryAnalysisRequest
    allowed = tmp_path / "repos"
    allowed.mkdir()
    monkeypatch.setattr(main.settings, "allowed_repo_roots", str(allowed))
    assert AgentRunRequest(repo_path=str(allowed / "project"), question="fix").repo_path == str(allowed / "project")
    for path in [allowed / ".." / "secret", tmp_path / "repos-other"]:
        with pytest.raises(ValidationError):
            AgentRunRequest(repo_path=str(path), question="fix")
        with pytest.raises(ValidationError):
            RepositoryAnalysisRequest(repo_path=str(path))
        with pytest.raises(ValidationError):
            GitHubIssueImportRequest(owner="o", repo="r", issue_number=1, local_repo_path=str(path))
    monkeypatch.setattr(connection, "DATABASE_PATH", tmp_path / "whitelist.db")
    with TestClient(app) as client:
        assert client.post("/api/tasks/plan", json={"repo_path": str(tmp_path), "question": "fix"}).status_code == 422
        old = task_repository.create_task(repo_path=str(tmp_path), question="old", plan=[])
        assert client.post(f"/api/tasks/{old.id}/execute").status_code == 403
        assert task_repository.get_task(old.id).status == "awaiting_approval"
        task_repository.claim_status(old.id, {"awaiting_approval"}, "interrupted")
        assert client.post(f"/api/tasks/{old.id}/resume").status_code == 403
        assert task_repository.get_task(old.id).status == "interrupted"
        assert client.get(f"/api/tasks/{old.id}/diff").status_code == 403


def test_repo_whitelist_resolves_symlink_escape(tmp_path, monkeypatch):
    """! @brief 授权目录内的符号链接不能把访问导向目录外。"""
    import pytest
    from backend.src.security import validate_repo_path
    allowed, outside = tmp_path / "allowed", tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    link = allowed / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        # Windows 非开发模式无符号链接权限时使用目录 junction。
        import subprocess
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        if result.returncode:
            pytest.skip("当前系统无法创建符号链接或 junction")
    monkeypatch.setattr(main.settings, "allowed_repo_roots", str(allowed))
    with pytest.raises(ValueError, match="授权范围"):
        validate_repo_path(str(link / "file.py"))
