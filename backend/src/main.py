import json
import shutil
import subprocess
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
from .tools.git_tool import git_diff


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """! @brief 统一管理应用启动和关闭生命周期。"""

    configure_logging()
    init_database()
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

    orchestrator =DevPilotOrchestrator(repo_path=request.repo_path)
    plan=orchestrator.plan(request.question)
    task = task_repository.create_task(
        repo_path=request.repo_path,
        question=request.question,
        plan=plan,
    )

    return TaskPlanResponse(task_id=task.id,plan=task.plan,status=task.status)

@app.post("/api/tasks/{task_id}/execute")
def execute_task(task_id: str) -> StreamingResponse:
    """执行已批准的任务（从 task_repository 取出计划，跑 execute_stream）。

    与 `/api/tasks/plan` 配合实现「先看计划 → 用户批准 → 再执行」的两段式流程。
    只有状态为 `awaiting_approval` 的任务才能执行，避免重复触发。

    Args:
        task_id: URL 路径参数，任务唯一标识（由 `/api/tasks/plan` 返回）。
            FastAPI 会自动把 URL 里 `/api/tasks/{task_id}/execute` 的那段
            解析成 `task_id` 形参并注入，无需在装饰器里 f-string 引用。

    Returns:
        SSE 流式响应（text/event-stream），实时推送 Coder/Tester/Reviewer 阶段事件。

    Raises:
        HTTPException: 任务不存在 (404) 或状态不允许执行 (409) 时抛出。
    """
    # 1. 取出任务 + 校验状态。
    try:
        task = task_repository.get_task(task_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    # Approval Gate：只有用户看过计划后，手动调用 execute 的任务才允许继续。
    # 当前版本把「调用 execute」本身视作人工批准动作，所以这里检查
    # awaiting_approval，而不是另外要求 approved 状态。
    if task.status != "awaiting_approval":
        raise InvalidTaskStateError(
            f"当前任务状态无法执行：{task.status}",
        )

    orchestrator = DevPilotOrchestrator(repo_path=task.repo_path)

    def event_generator() -> Iterator[str]:
        """把 orchestrator 的执行过程转成 SSE 事件流推给前端。

        流程：
        1. 逐条消费 `execute_stream(question, plan)` 产出的事件，序列化为
           `"data: {json}\\n\\n"` 的 SSE 字符串（前端 EventSource 逐条接收）；
        2. 整条流水线正常跑完 → 把任务置为 completed；
        3. 任何环节抛异常 → 把任务置为 failed，并额外推一条 error 事件
           （type=error）给前端，避免连接中断后前端一片空白。

        Yields:
            str: 每条 SSE 消息字符串（不含 SSE 的结束分隔符之外的额外内容）。
        """
        sequence = 0
        # 一旦 SSE 生成器开始消费，说明任务正式进入 Coder/Tester/Reviewer 流程。
        task_repository.set_status(
            task_id,
            "running",
        )
        try:
            saw_error = False
            for event in orchestrator.execute_stream(
                question=task.question,
                plan=task.plan,
            ):
                # sequence 是“单个任务内”的严格递增序号。数据库查询按它排序，
                # 因此即使多个事件发生在同一秒，也能还原真实执行先后顺序。
                sequence += 1
                if event.type == "error":
                    saw_error = True
                task_repository.add_event(
                    task_id=task_id,
                    sequence=sequence,
                    event=event,
                )
                # tool_result 已由 BaseToolAgent 带上工具名、原始参数和结果摘要。
                # 在这里集中落库，Agent 本身无需知道 SQLite 的存在。
                if event.type == "tool_result":
                    tool = event.data.get(
                        "tool",
                    )
                    arguments = event.data.get(
                        "arguments",
                        {},
                    )
                    result_preview = event.data.get(
                        "result_preview",
                        "",
                    )
                    if isinstance(tool, str) and isinstance(arguments, dict):
                        task_repository.add_tool_call(
                            task_id=task_id,
                            agent=event.agent,
                            iteration=event.iteration,
                            tool=tool,
                            arguments=arguments,
                            result_preview=str(result_preview),
                        )
                # mode="json" 会把 Pydantic 中可能存在的日期、枚举等值转换为
                # JSON 兼容类型；随后再包装成 SSE 要求的 data: ...\n\n 格式。
                event_data = event.model_dump(mode="json")
                yield "data: " + json.dumps(event_data, ensure_ascii=False) + "\n\n"
            # 整条流水线跑完后，根据是否出现 error 事件设置最终状态。
            task_repository.set_status(
                task_id,
                "failed" if saw_error else "completed",
            )
        except Exception as exc:
            # 任何异常都把任务置为 failed，并通过 SSE 推一条 error 事件给前端。
            task_repository.set_status(task_id, "failed")
            sequence += 1
            error_event = AgentEvent(
                type="error",
                agent="orchestrator",
                message=str(exc),
            )
            task_repository.add_event(
                task_id=task_id,
                sequence=sequence,
                event=error_event,
            )
            yield "data: " + error_event.model_dump_json() + "\n\n"

    return StreamingResponse(
        content=event_generator(),
        media_type="text/event-stream",
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
            repo_path=request.local_repo_path
        )
        plan=orchestrator.plan(
            question=question
        )
        task=task_repository.create_task(
            repo_path=request.local_repo_path,
            question=question,
            plan=plan,
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
