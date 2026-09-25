from typing import Any

from pydantic import BaseModel, Field


class GitHubIssueRef(BaseModel):
    """! @brief GitHub Issue 的最小引用信息。

    只保存定位一个 Issue 必需的三元组，适合在接口参数、
    日志或任务元数据中传递。
    """

    owner: str = Field(
        ...,
        description="仓库所属 GitHub 用户或组织名",
    )

    repo: str = Field(
        ...,
        description="GitHub 仓库名称",
    )

    issue_number: int = Field(
        ...,
        description="Issue 编号",
    )


class GitHubIssueContext(BaseModel):
    """! @brief DevPilot 内部使用的标准化 Issue 上下文。

    GitHub MCP 返回的数据结构可能比较复杂，本模型把后续 Planner/Agent
    真正需要的信息整理成稳定结构，避免业务层直接依赖 MCP 原始返回格式。
    """

    owner: str = Field(
        ...,
        description="仓库所属 GitHub 用户或组织名",
    )

    repo: str = Field(
        ...,
        description="GitHub 仓库名称",
    )

    issue_number: int = Field(
        ...,
        description="Issue 编号",
    )

    title: str = Field(
        ...,
        description="Issue 标题",
    )

    body: str = Field(
        default="",
        description="Issue 正文描述",
    )

    state: str = Field(
        default="",
        description="Issue 当前状态，例如 open/closed",
    )

    url: str = Field(
        default="",
        description="Issue 网页 URL",
    )

    comments: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Issue 评论列表，保留 GitHub 返回的评论字典",
    )

    raw: dict[str, Any] = Field(
        default_factory=dict,
        description="GitHub MCP 返回的原始 Issue 数据，便于排查兼容问题",
    )
