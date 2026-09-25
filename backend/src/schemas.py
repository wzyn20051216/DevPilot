from typing import Any

from pydantic import BaseModel, Field

from .models.agent_state import ToolCallRecord
from .models.agent_state import PlanStep
from .models.publish import PublishPreview
from .models.task import DevelopmentTask


class ChatRequest(BaseModel):
    """用户发送给 DevPilot 的聊天请求。"""

    message: str = Field(..., min_length=1, description="用户输入的信息")


class ChatResponse(BaseModel):
    """DevPilot 返回给用户的聊天响应。"""

    reply: str = Field(..., description="DevPilot 返回给用户的回复内容")



#添加一个新的请求和响应模型，用于分析代码仓库。
class RepositoryAnalysisRequest(BaseModel):
    """请求数据模型，用于分析代码仓库。"""

    repo_path: str = Field(..., min_length=1, description="仓库路径")
    question: str = Field(default="分析这个项目的整体架构", min_length=1, description="用户问题")

class RepositoryAnalysisResponse(BaseModel):
    """响应数据模型，包含仓库分析结果。"""

    answer: str = Field(..., description="LLM 的回答")




class AgentRunRequest(BaseModel):
    """请求数据模型，用于运行代码分析 Agent。"""

    repo_path: str = Field(..., min_length=1, description="仓库路径")
    question: str = Field(..., min_length=1, description="用户问题")

class AgentRunResponse(BaseModel):
    """响应数据模型，包含 Agent 的运行结果。"""

    answer: str = Field(..., description="LLM 的回答")
    tool_calls: list[ToolCallRecord] = Field(..., description="工具调用历史记录")
    # 注意：用单数 iteration，与 AgentEvent final 事件 data 里的 "iteration" 键名一致
    iteration: int = Field(..., description="迭代次数")

class TaskPlanResponse(BaseModel):
    """! @brief 创建任务计划后的响应模型。"""

    task_id: str = Field(
        ...,
        description="持久化任务 ID",
    )
    status: str = Field(
        ...,
        description="任务状态",
    )
    plan: list[PlanStep] = Field(
        ...,
        description="Planner 生成的执行步骤",
    )


class TaskDetailResponse(BaseModel):
    """任务详情响应。"""

    task: DevelopmentTask
    source: dict[str, Any] | None = Field(
        default=None,
        description="任务来源信息，例如 GitHub Issue；普通手动任务为空",
    )
    events: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]


class GitHubIssueImportRequest(
    BaseModel
):
    """! @brief 从 GitHub Issue 创建 DevPilot 任务的请求模型。"""

    owner: str = Field(
        ...,
        min_length=1,
        description="仓库所属 GitHub 用户或组织名",
    )

    repo: str = Field(
        ...,
        min_length=1,
        description="GitHub 仓库名称",
    )

    issue_number: int = Field(
        ...,
        ge=1,
        description="Issue 编号",
    )

    local_repo_path: str = Field(
        ...,
        min_length=1,
        description="本地代码仓库路径，DevPilot 会在该仓库上执行分析和修改",
    )


class GitHubIssueImportResponse(
    BaseModel
):
    """! @brief GitHub Issue 导入成功后的响应模型。"""

    task_id: str = Field(
        ...,
        description="创建出的 DevPilot 任务 ID",
    )

    status: str = Field(
        ...,
        description="任务初始状态",
    )

    issue_title: str = Field(
        ...,
        description="GitHub Issue 标题",
    )

    issue_url: str = Field(
        ...,
        description="GitHub Issue 网页 URL",
    )

    plan: list[PlanStep] = Field(
        ...,
        description="根据 Issue 生成的开发计划",
    )


class PublishPreviewRequest(
    BaseModel
):
    """! @brief 创建发布预览的请求模型。

    发布预览只生成待审批内容，不会真正推送 GitHub。
    """

    base_branch: str = Field(
        default="main",
        min_length=1,
        description="Pull Request 目标分支，默认 main",
    )


class PublishPreviewResponse(
    BaseModel
):
    """! @brief 创建发布预览后的响应模型。"""

    preview: PublishPreview = Field(
        ...,
        description="发布前人工审批预览",
    )


class PublishResponse(
    BaseModel
):
    """! @brief 真正发布后的响应模型。"""

    preview: PublishPreview = Field(
        ...,
        description="发布完成后的预览记录，包含最终状态和 PR URL",
    )

    result: dict[str, object] = Field(
        default_factory=dict,
        description="GitHub MCP 写工具返回的发布结果摘要",
    )
