from pydantic import BaseModel, Field


class PullRequestDraft(BaseModel):
    """! @brief Pull Request 草稿模型。

    PullRequestAgent 会根据 Issue、Git diff 和测试结果生成该结构。
    当前模型只表示"草稿内容"，不负责真正调用 GitHub 创建 PR。
    """

    title: str = Field(
        ...,
        description="PR 标题",
    )

    body: str = Field(
        ...,
        description="PR 正文，包含修改摘要、测试结果和风险说明",
    )

    changed_files: list[str] = Field(
        default_factory=list,
        description="本次 PR 涉及的文件列表",
    )

    tests: str = Field(
        default="",
        description="测试执行摘要",
    )

    commit_message: str = Field(
        ...,
        description="建议的提交信息，用于后续人工确认后执行 git commit",
    )
