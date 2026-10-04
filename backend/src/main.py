import json
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger

from .agents.pr_agent import PullRequestAgent
from .database.publish_repository import publish_repository
from .database.connection import get_connection, init_database
from .exceptions import DevPilotError, InvalidTaskStateError
from .llm_client import chat_once
from .logging_config import configure_logging
from .config import settings
from .services.task_policy import decide_task_strategy, task_rag_enabled
from .schemas import (
    AgentRunRequest,
    ChatRequest,
    ChatResponse,
    GitHubIssueImportRequest,
    GitHubIssueImportResponse,
    PublishPreviewRequest,
    PublishPreviewResponse,
    PublishResponse,
    RepositoryAnalysisRequest,
    RepositoryAnalysisResponse,
    TaskDetailResponse,
    TaskPlanResponse,
)
from .services.repository_analyzer import analyze_repository
from .agents.code_agent import CodeAgent
from .agents.orchestrator import DevPilotOrchestrator
from .database.task_repository import task_repository
from .models.agent_state import AgentEvent
from .services.github_issue_service import (
    github_issue_service,
    build_development_request,
)
from .services.publish_service import publish_service
from .services.evaluation_service import evaluation_service
from .services.task_execution_service import (
    SingleAgentTaskRunner,
    TaskExecutionService,
    encode_sse_event,
)
from .services.trace_service import trace_service
from .tools.git_tool import git_diff


def _create_task_runner(task, cancel_check):
    """! @brief 按任务持久化的执行策略构造实际运行器。"""

    enable_rag = task_rag_enabled(task)
    if task.execution_mode.startswith("single_"):
        return SingleAgentTaskRunner(
            repo_path=task.repo_path,
            enable_rag=enable_rag,
            cancel_check=cancel_check,
            strategy=decide_task_strategy(task),
        )
    return DevPilotOrchestrator(
        repo_path=task.repo_path,
        enable_rag=enable_rag,
        cancel_check=cancel_check,
    )


task_execution_service = TaskExecutionService(
    repository=task_repository,
    orchestrator_factory=_create_task_runner,
)

TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled", "interrupted"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """! @brief 统一管理应用启动和关闭生命周期。"""

    configure_logging()
    init_database()
    # 队列任务由独立 Worker 持有，API 重启不能更改其执行状态。
    interrupted = (
        task_repository.mark_incomplete_as_interrupted()
        if settings.task_queue_backend == "inline"
        else 0
    )
    if interrupted:
        logger.warning("Marked {} unfinished tasks as interrupted", interrupted)
    logger.info(
        "{} API started in {} mode",
        settings.app_name,
        settings.app_env,
    )
    yield
    logger.info("{} API stopped", settings.app_name)


# FastAPI 应用实例，启动副作用统一放在 lifespan 中。
app = FastAPI(
    title="DevPilot API",
    description="Multi-Agent Software Engineering Platform",
    version="1.0.0",
    lifespan=lifespan,
)

# Vite 开发服务器和 FastAPI 使用不同端口，浏览器会把它们视为跨域请求。
# 来源列表由环境变量配置，生产部署不需要修改源码。
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.exception_handler(DevPilotError)
async def devpilot_error_handler(
    _request: Request,
    exc: DevPilotError,
) -> JSONResponse:
    """! @brief 把业务异常转换为稳定、可供前端处理的错误结构。"""

    logger.warning("{}: {}", exc.code, exc.message)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
            }
        },
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    """! @brief 收口未预期异常，并在生产环境隐藏内部实现细节。"""

    logger.exception("Unhandled exception: {}", type(exc).__name__)
    message = str(exc) if settings.app_env == "development" else "Internal server error"
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": message,
            }
        },
    )


def _extract_pr_url(
    publish_result: dict[str, object],
) -> str:
    """! @brief 从 GitHub MCP 返回结果中提取 PR URL。

    GitHub MCP 不同版本可能返回 dict，也可能返回文本。本函数只做保守提取：
    找到 html_url / url / pull_request_url 这类字段就返回，否则返回空字符串。

    @param publish_result PublishService.publish() 返回的结果摘要。
    @return Pull Request URL；无法识别时返回空字符串。
    """

    pr_result = publish_result.get(
        "pull_request",
    )

    if isinstance(pr_result, dict):
        pr_data = cast(
            dict[str, object],
            pr_result,
        )
        for key in (
            "html_url",
            "url",
            "pull_request_url",
        ):
            value = pr_data.get(key)
            if isinstance(value, str) and value:
                return value

    if isinstance(pr_result, str) and pr_result.startswith(
        "http",
    ):
        return pr_result

    return ""


@app.get("/healthz")
async def health_check():
    """健康检查接口：确认服务在跑、能响应。"""
    return {"status": "ok", "service": "DevPilot"}


def _is_docker_available() -> bool:
    """! @brief 检查 Docker CLI 与 daemon 是否都可用。

    探针设置较短超时，避免 Docker Desktop 未启动时拖慢 `/readyz`。Docker
    是可降级能力，因此检查失败只会反映在响应明细中，不影响数据库就绪状态。
    """

    docker_cli = shutil.which("docker")
    if docker_cli is None:
        return False

    try:
        result = subprocess.run(
            [docker_cli, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False

    return result.returncode == 0


@app.get("/readyz")
def readiness_check() -> JSONResponse:
    """! @brief 检查数据库硬依赖及 Docker Sandbox 可用性。

    SQLite 不可用时返回 503；Docker 作为可降级能力只进入 checks，不阻止
    Planner、仓库读取和历史任务查询继续提供服务。
    """

    checks = {
        "database": False,
        "docker": _is_docker_available(),
    }
    try:
        with get_connection() as conn:
            _ = conn.execute("SELECT 1").fetchone()
        checks["database"] = True
    except Exception as exc:
        logger.error("Readiness database check failed: {}", type(exc).__name__)

    ready = checks["database"]
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "checks": checks,
        },
    )


@app.get("/api/evals/summary")
def get_eval_summary(run_id: str | None = None):
    """! @brief 返回 Evaluation Dashboard 的聚合指标。

    @param run_id 可选实验批次 ID；不传时汇总数据库中的全部实验。
    @return 按四种 variant 分组的成功率、工具、迭代、耗时和 Token 指标。
    """

    return evaluation_service.summary(run_id=run_id)


@app.get("/api/evals/difficulty")
def get_eval_difficulty_summary(run_id: str | None = None):
    """! @brief 按 easy/medium/hard 和 variant 返回正式实验汇总。"""

    return evaluation_service.difficulty_summary(run_id=run_id)


@app.get("/api/evals/ablation")
def get_eval_ablation_report(run_id: str | None = None):
    """! @brief 返回 RAG 与多 Agent 的配对消融、置信区间和 p 值。"""

    try:
        return evaluation_service.ablation_report(run_id=run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/chat", response_model=ChatResponse)
async def chat(chat_request: ChatRequest):
    """处理前端用户消息，走单轮 LLM 对话（无工具、无多智能体）。

    Args:
        chat_request: 含用户 message 的请求体。

    Returns:
        含 reply 的响应。
    """
    try:
        reply = chat_once(chat_request.message)
        return ChatResponse(reply=reply)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/repository/analyze", response_model=RepositoryAnalysisResponse)
async def analyze_repo(request: RepositoryAnalysisRequest):
    """分析代码仓库并返回结果（构建上下文 + 单轮 LLM 分析）。

    Args:
        request: 含仓库路径和用户问题的请求体。

    Returns:
        含 LLM 分析回答的响应。
    """
    try:
        answer = analyze_repository(repo_path=request.repo_path, question=request.question)
        return RepositoryAnalysisResponse(answer=answer)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/agent/stream")
def stream_agent(request: AgentRunRequest) -> StreamingResponse:
    """单 Agent 流式接口：用 CodeAgent 跑，SSE 实时推事件。

    Args:
        request: 含仓库路径和用户问题的请求体。

    Returns:
        SSE 流式响应（text/event-stream）。
    """
    agent = CodeAgent(repo_path=request.repo_path)

    def event_generator() -> Iterator[str]:
        # 生成器函数：逐个把 agent.run_stream 产出的事件
        # 序列化成 SSE 字符串（"data: {...}\n\n"）推给前端。
        for event in agent.run_stream(question=request.question):
            event_data = event.model_dump(mode="json")
            yield "data: " + json.dumps(event_data, ensure_ascii=False) + "\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event_stream",  # SSE 固定 MIME 类型
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/multi-agent/stream")
def multi_agent_stream(request: AgentRunRequest) -> StreamingResponse:
    """多智能体流式接口：用 DevPilotOrchestrator 编排四个角色 agent，SSE 推事件。

    Args:
        request: 含仓库路径和用户问题的请求体。

    Returns:
        SSE 流式响应（text/event-stream）。
    """
    # 创建编排器：内部会实例化 planner/coder/tester/reviewer 四个 agent。
    orchestrator = DevPilotOrchestrator(repo_path=request.repo_path)

    def event_generator():
        # 和 /api/agent/stream 相同的 SSE 序列化逻辑，
        # 只是事件来源从单 agent 换成了 orchestrator 的多智能体流水线。
        for event in orchestrator.run_stream(question=request.question):
            event_data = event.model_dump(mode="json")
            yield "data: " + json.dumps(event_data, ensure_ascii=False) + "\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )

@app.post("/api/tasks/plan",response_model=TaskPlanResponse)
def create_task_plan(request:AgentRunRequest)->TaskPlanResponse:
    """! @brief 只生成计划并创建待审批任务。

    该接口不会修改代码。它只让 Planner 根据用户问题生成执行计划，
    然后把任务保存为 awaiting_approval，等待用户检查计划后再调用 execute。

    @param request 本地仓库路径和用户任务描述。
    @return 任务 ID、awaiting_approval 状态和计划步骤。
    """

    orchestrator = DevPilotOrchestrator(
        repo_path=request.repo_path,
        enable_rag=request.execution_mode in {"single_rag", "multi_rag"},
    )
    plan=orchestrator.plan(request.question)
    task = task_repository.create_task(
        repo_path=request.repo_path,
        question=request.question,
        plan=plan,
        execution_mode=request.execution_mode,
    )

    return TaskPlanResponse(task_id=task.id,plan=task.plan,status=task.status)

def _enqueue_task_for_worker(task_id: str, task) -> StreamingResponse:
    """! @brief 队列模式（10.2.5）：API 只入队，由独立 Sandbox Worker 领取执行。

    任务状态保持 awaiting_approval，Worker 通过 claim_status 原子迁移到
    running 后执行。task_id 唯一约束保证重复调用本端点不会重复执行；
    对 done/dead 的历史队列记录用 reset 回收，支持失败后重新执行。
    """
    from .services.task_queue import get_task_queue

    queue = get_task_queue()
    idempotency_key = f"{task_id}:{task.status}"
    if not queue.enqueue(task_id, idempotency_key=idempotency_key):
        entry = queue.get(task_id)
        entry_status = entry.status if entry else "unknown"
        if entry_status not in {"queued", "claimed"} and not queue.reset(
            task_id,
            idempotency_key,
        ):
            raise InvalidTaskStateError(
                f"当前任务无法入队执行：队列状态 {entry_status}",
            )
    return _task_event_stream(task_id, after_sequence=0)


@app.post("/api/tasks/{task_id}/execute")
def execute_task(task_id: str) -> StreamingResponse:
    """! @brief 原子领取任务、后台执行，并流式观察持久化事件。

    ``TASK_QUEUE_BACKEND=inline``（默认）时行为与历史版本完全一致；
    配置为 sqlite/redis 时本端点只把任务写入持久化队列，执行由独立
    Worker 进程完成（租约 + 心跳 + 幂等），SSE 仍然只读持久化事件。
    """

    try:
        task = task_repository.get_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if settings.task_queue_backend != "inline":
        if task.status != "awaiting_approval":
            raise InvalidTaskStateError(f"当前任务状态无法执行：{task.status}")
        return _enqueue_task_for_worker(task_id, task)

    if not task_repository.claim_status(
        task_id,
        {"awaiting_approval"},
        "running",
    ):
        current = task_repository.get_task(task_id)
        raise InvalidTaskStateError(
            f"当前任务状态无法执行：{current.status}",
        )
    try:
        task_execution_service.start(task_id)
    except Exception:
        task_repository.claim_status(task_id, {"running"}, "failed")
        raise

    return _task_event_stream(task_id, after_sequence=0)


def _task_event_stream(task_id: str, after_sequence: int) -> StreamingResponse:
    """! @brief 从 SQLite 追踪新增事件，客户端断线不影响后台执行。"""

    def event_generator() -> Iterator[str]:
        cursor = after_sequence
        while True:
            events = task_repository.get_events_after(task_id, cursor)
            for event in events:
                cursor = int(event["sequence"])
                yield encode_sse_event(event)
            status = task_repository.get_task(task_id).status
            if status in TERMINAL_TASK_STATUSES and not events:
                pending = False
                if settings.task_queue_backend != "inline" and status in {"interrupted", "failed"}:
                    from .services.task_queue import get_task_queue

                    entry = get_task_queue().get(task_id)
                    pending = entry is not None and entry.status in {"queued", "claimed"}
                if not pending:
                    return
            time.sleep(0.1)

    return StreamingResponse(
        content=event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/tasks/{task_id}/events")
def reconnect_task_events(task_id: str, after_sequence: int = 0) -> StreamingResponse:
    """! @brief 断线后从指定事件序号恢复 SSE。"""

    _ = task_repository.get_task(task_id)
    return _task_event_stream(task_id, after_sequence=max(0, after_sequence))


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> dict[str, str]:
    """! @brief 请求协作式取消正在执行的任务。"""

    task = task_repository.get_task(task_id)
    if settings.task_queue_backend != "inline" and task.status == "awaiting_approval":
        # 队列模式下取消尚未被 Worker 领取的任务：移出队列并置 cancelled。
        from .services.task_queue import get_task_queue

        if task_repository.claim_status(task_id, {"awaiting_approval"}, "cancelled"):
            get_task_queue().remove(task_id)
            return {"task_id": task_id, "status": "cancelled"}
        raise InvalidTaskStateError("任务状态已变化，请刷新后重试")
    # 队列模式下 running 任务的取消：API 只标记 cancelling，Worker 的
    # 取消轮询会消费该状态并触发其进程内的协作式取消（见 worker.py）。
    if task.status != "running":
        raise InvalidTaskStateError(f"当前任务状态无法取消：{task.status}")
    if not task_repository.claim_status(task_id, {"running"}, "cancelling"):
        raise InvalidTaskStateError("任务状态已变化，请刷新后重试")
    if settings.task_queue_backend == "inline" and not task_execution_service.cancel(task_id):
        task_repository.claim_status(task_id, {"cancelling"}, "interrupted")
        raise InvalidTaskStateError("任务不在当前进程中运行，已标记为 interrupted")
    return {"task_id": task_id, "status": "cancelling"}


@app.post("/api/tasks/{task_id}/resume")
def resume_task(task_id: str) -> StreamingResponse:
    """! @brief 恢复服务重启时中断的任务。

    若存在上下文检查点，则从检查点恢复中断前的模型上下文继续执行；
    否则从已持久化的批准计划重新运行 Agent。检查点注入由执行服务统一处理，
    本端点只负责原子领取并流式观察，与 execute 走同一路径。
    """

    _ = task_repository.get_task(task_id)
    if settings.task_queue_backend != "inline":
        # 队列模式：恢复执行交给 Worker（其 claim_status 接受 interrupted），
        # 上下文检查点注入同样由 Worker 进程的执行服务统一处理。
        task = task_repository.get_task(task_id)
        if task.status != "interrupted":
            raise InvalidTaskStateError(f"当前任务状态无法恢复：{task.status}")
        return _enqueue_task_for_worker(task_id, task)
    if not task_repository.claim_status(task_id, {"interrupted"}, "running"):
        current = task_repository.get_task(task_id)
        raise InvalidTaskStateError(f"当前任务状态无法恢复：{current.status}")
    # 必须在启动线程前固定游标。否则极快的 Agent 可能先写入首条恢复事件，
    # 随后读取的 next sequence 会把它误当成历史事件，导致当前 SSE 漏报。
    resume_after_sequence = task_repository.next_event_sequence(task_id) - 1
    try:
        task_execution_service.start(task_id)
    except Exception:
        task_repository.claim_status(task_id, {"running"}, "interrupted")
        raise
    return _task_event_stream(
        task_id,
        after_sequence=resume_after_sequence,
    )

@app.get("/api/tasks/{task_id}", response_model=TaskDetailResponse)
def get_task_detail(task_id:str)->TaskDetailResponse:
    """! @brief 查询任务详情、执行事件和工具调用记录。

    @param task_id 任务 ID。
    @return 任务主体、SSE 事件轨迹和自动记录的 tool call 列表。
    @raise HTTPException 任务不存在时返回 404。
    """

    try:
        task=task_repository.get_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    return TaskDetailResponse(
        task=task,
        source=task_repository.get_source(
            task_id
        ),
        events=(
            task_repository.get_events(
                task_id
            )
        ),
        tool_calls=(
            task_repository.get_tool_calls(
                task_id
            )
        ),
    )


@app.get("/api/tasks/{task_id}/metrics")
def get_task_metrics(task_id: str) -> dict[str, object]:
    """! @brief 返回任务级 Token、成本、模型耗时和工具耗时。"""

    return trace_service.task_metrics(task_id)


@app.get("/api/tasks/{task_id}/diff")
def get_task_diff(task_id: str) -> dict[str, str]:
    """! @brief 返回任务仓库当前尚未提交的 Git Diff。

    该接口只读取任务绑定的本地仓库，不执行写文件、提交或推送操作。
    前端在任务执行完成后调用它，为人工复核提供与当前工作区一致的差异。

    @param task_id 任务 ID。
    @return 包含 ``diff`` 文本的字典；仓库没有变更时返回空字符串。
    @raise HTTPException 任务不存在时返回 404，Git 命令失败时返回 500。
    """

    try:
        task = task_repository.get_task(task_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    try:
        result = git_diff(repo_path=task.repo_path)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc

    return {
        "diff": str(result.get("diff", "")),
    }


@app.post("/api/github/issues/import",response_model=GitHubIssueImportResponse)
def import_github_issue(request:GitHubIssueImportRequest)->GitHubIssueImportResponse:
    """! @brief 从 GitHub Issue 导入并创建 DevPilot 开发任务。

    接口流程：
    1. 通过 GitHub MCP 读取 Issue 和评论；
    2. 将 Issue 转成 DevPilot 开发请求；
    3. 调用 Planner 生成计划；
    4. 将任务和计划写入 SQLite。

    @param request GitHub Issue 定位信息和本地仓库路径。
    @return 创建出的任务 ID、Issue 信息和计划步骤。
    @raise HTTPException 导入、规划或落库失败时返回 500。
    """

    try:
        issue=github_issue_service.fetch(
            owner=request.owner,
            repo=request.repo,
            issue_number=request.issue_number,
        )
        question=build_development_request(
            issue
        )
        orchestrator=DevPilotOrchestrator(
            repo_path=request.local_repo_path,
            enable_rag=request.execution_mode in {"single_rag", "multi_rag"},
        )
        plan=orchestrator.plan(
            question=question
        )
        task=task_repository.create_task(
            repo_path=request.local_repo_path,
            question=question,
            plan=plan,
            execution_mode=request.execution_mode,
        )
        # GitHub Issue 只作为任务来源记录保存。此处不会执行 Coder，
        # 返回给用户的是 awaiting_approval 状态和 Planner 计划。
        task_repository.set_source(
            task_id=task.id,
            source_type="github_issue",
            source={
                "owner": issue.owner,
                "repo": issue.repo,
                "issue_number": (
                    issue.issue_number
                ),
                "title": issue.title,
                "url": issue.url,
            },
        )
        return (
                GitHubIssueImportResponse(
                    task_id=task.id,
                    status=task.status,
                    issue_title=(
                        issue.title
                    ),
                    issue_url=(
                        issue.url
                    ),
                    plan=task.plan,
                )
        )
    except Exception as exc:
        raise HTTPException(status_code=500,detail=str(exc))from exc


@app.post("/api/tasks/{task_id}/publish-preview",response_model=PublishPreviewResponse)
def create_publish_preview(task_id:str,request:PublishPreviewRequest)->PublishPreviewResponse:
    """! @brief 为已完成任务生成发布前人工审批预览。

    该接口不会推送 GitHub，也不会创建 PR。它只做四件事：
    1. 校验任务已经 completed；
    2. 读取任务来源中的 GitHub Issue 信息；
    3. 根据 diff / 测试摘要生成 PR 草稿；
    4. 收集待发布文件并保存 PublishPreview。

    @param task_id 任务 ID。
    @param request 发布预览参数，目前主要是 base_branch。
    @return 发布前审批预览。
    @raise HTTPException 任务状态不允许、缺少 GitHub Issue 来源或生成失败时抛出。
    """

    try:
        task=task_repository.get_task(task_id=task_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    if task.status != "completed":
        raise InvalidTaskStateError(
            "只有已经完成测试和 Review 的任务才能进入发布阶段",
        )

    source_record = (
        task_repository.get_source(
            task_id
        )
    )
    if (
        source_record is None
        or source_record.get("source_type") != "github_issue"
        or not isinstance(source_record.get("source"), dict)
    ):
        raise HTTPException(
            status_code=400,
            detail="发布预览需要任务来源为 GitHub Issue",
        )

    # 上面 isinstance 已经做过运行时校验；这里 cast 是给静态类型检查器看的，
    # 让 basedpyright 知道下面可以安全使用 .get()。
    issue_source = cast(
        dict[str, object],
        source_record["source"],
    )
    owner = str(
        issue_source.get(
            "owner",
            "",
        )
    )
    repo = str(
        issue_source.get(
            "repo",
            "",
        )
    )
    issue_title = str(
        issue_source.get(
            "title",
            "",
        )
    )
    issue_number_raw = issue_source.get(
        "issue_number",
        0,
    )
    # SQLite JSON 中的数字通常仍是 int，但旧数据或外部导入可能保存成字符串。
    # 这里只接受纯数字字符串，避免 int("12a") 一类异常进入发布流程。
    if isinstance(issue_number_raw, int):
        issue_number = issue_number_raw
    elif isinstance(issue_number_raw, str) and issue_number_raw.isdigit():
        issue_number = int(issue_number_raw)
    else:
        issue_number = 0

    if not owner or not repo or issue_number <= 0:
        raise HTTPException(
            status_code=400,
            detail="GitHub Issue 来源信息不完整，无法生成发布预览",
        )

    diff_result=git_diff(repo_path=task.repo_path)
    draft = PullRequestAgent().generate(
        issue_title=issue_title,
        issue_number=issue_number,
        diff=str(
            diff_result.get(
                "diff",
                "",
            )
        ),
        test_summary="测试和 reviewer 已经通过",
    )
    preview = (
        publish_service
        .create_preview(
            task_id=task_id,
            repo_path=task.repo_path,
            owner=owner,
            repo=repo,
            base_branch=(
                request.base_branch
            ),
            pr_title=draft.title,
            pr_body=draft.body,
            commit_message=(
                draft.commit_message
            ),
        )
    )
    preview = publish_repository.save_preview(
        preview
    )
    return PublishPreviewResponse(
        preview=preview
    )


@app.post(
    "/api/tasks/{task_id}/publish",
    response_model=PublishResponse,
)
def publish_task(
    task_id: str,
) -> PublishResponse:
    """! @brief 用户确认发布预览后，真正推送 GitHub 并创建 Draft PR。

    该接口表示用户已经确认 `/publish-preview` 中展示的修改。执行前会再次
    读取本地文件并计算 snapshot hash；如果审批后代码发生变化，则返回 409，
    要求重新生成 Publish Preview，防止发布内容和审批内容不一致。

    @param task_id 任务 ID。
    @return 发布后的预览记录和 GitHub MCP 返回结果摘要。
    @raise HTTPException 任务、预览不存在，状态不允许，或发布失败时抛出。
    """

    try:
        task = task_repository.get_task(
            task_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    try:
        preview = publish_repository.get_preview(
            task_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    if preview.status != "awaiting_approval":
        raise InvalidTaskStateError(
            "当前发布状态不可执行: "
            f"{preview.status}",
        )

    try:
        # 必须先校验快照，再把状态改成 publishing。若审批后的本地文件已变化，
        # 请求会以 409 结束，预览仍保持 awaiting_approval，便于重新生成审批。
        current_files = publish_service.verify_snapshot(
            task,
            preview,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    preview = publish_repository.set_status(
        task_id,
        "publishing",
    )

    try:
        publish_result = publish_service.publish(
            task=task,
            preview=preview,
            current_files=current_files,
        )

        pr_url = _extract_pr_url(
            publish_result
        )

        # GitHub MCP 的不同版本不一定都返回可识别 URL；创建 PR 成功但无法
        # 提取 URL 时仍保留完整 publish_result，不能因此误判发布失败。
        if pr_url:
            preview = publish_repository.set_pr_url(
                task_id,
                pr_url,
            )

        preview = publish_repository.set_status(
            task_id,
            "published",
        )

        return PublishResponse(
            preview=preview,
            result=publish_result,
        )

    except Exception as exc:
        # publishing 之后任一 GitHub 步骤失败都收口为 failed，避免记录长期
        # 卡在“发布中”。远端可能已有分支，重试前应先检查 GitHub 实际状态。
        _ = publish_repository.set_status(
            task_id,
            "failed",
        )
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc
