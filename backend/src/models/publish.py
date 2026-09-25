from typing import Literal

from pydantic import BaseModel, Field


PublishStatus = Literal[
    # 预览已生成，等待用户人工确认是否发布。
    "awaiting_approval",
    # 用户批准后，正在推送分支或创建 PR。
    "publishing",
    # PR 已成功创建。
    "published",
    # 发布过程中失败。
    "failed",
]


class PublishPreview(BaseModel):
    """! @brief 代码发布前的人工审批内容。

    PublishPreview 是真正推送 GitHub 前给用户看的"发布确认单"：
    它包含待发布文件、目标分支、PR 草稿和快照 hash。用户确认后，
    发布服务才能继续执行 create_branch / push_files / create_pull_request。
    """

    task_id: str = Field(
        ...,
        description="关联的 DevPilot 任务 ID",
    )

    owner: str = Field(
        ...,
        description="GitHub 仓库 owner，即用户或组织名",
    )

    repo: str = Field(
        ...,
        description="GitHub 仓库名称",
    )

    base_branch: str = Field(
        ...,
        description="PR 目标分支，例如 main",
    )

    head_branch: str = Field(
        ...,
        description="DevPilot 将要推送的源分支",
    )

    commit_message: str = Field(
        ...,
        description="建议提交信息",
    )

    pr_title: str = Field(
        ...,
        description="PR 标题",
    )

    pr_body: str = Field(
        ...,
        description="PR 正文",
    )

    files: list[str] = Field(
        default_factory=list,
        description="本次发布包含的相对文件路径",
    )

    snapshot_hash: str = Field(
        ...,
        description="待发布文件内容快照 hash，用于发布前二次校验",
    )

    status: PublishStatus = Field(
        default="awaiting_approval",
        description="发布预览状态",
    )

    pr_url: str = Field(
        default="",
        description="发布成功后的 GitHub PR URL",
    )
