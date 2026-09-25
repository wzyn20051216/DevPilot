"""! @brief Pull Request 草稿生成 Agent。

该 Agent 不直接调用 GitHub 创建 PR，只根据 Issue 标题、Issue 编号、
Git diff 和测试结果生成一个结构化 PR Draft。这样可以先给用户审阅，
后续再决定是否真正创建 PR。
"""

from openai import OpenAI

from ..config import settings
from ..llm_client import create_client
from ..models.pull_request import (
    PullRequestDraft,
)
from ..services.structured_output import (
    parse_structured_output,
)


class PullRequestAgent:
    """! @brief 根据开发结果生成 PR 草稿的轻量 Agent。"""

    def __init__(self) -> None:
        """! @brief 初始化 LLM 客户端。"""

        self.client: OpenAI = create_client()

    def generate(
        self,
        issue_title: str,
        issue_number: int,
        diff: str,
        test_summary: str,
    ) -> PullRequestDraft:
        """! @brief 生成结构化 Pull Request 草稿。

        @param issue_title GitHub Issue 标题。
        @param issue_number GitHub Issue 编号。
        @param diff 当前本地仓库的 Git diff。
        @param test_summary Tester 阶段输出的测试摘要。
        @return 结构化 PR 草稿。
        """

        # 要求模型只输出 JSON，随后统一交给 parse_structured_output 校验。
        # 这里不让模型直接创建 PR，避免在代码未被用户确认前产生远端副作用。
        system_prompt = """
你是 DevPilot 的 Pull Request Writer。
根据 Issue、代码 Diff 和测试结果生成专业 PR 草稿。
不要编造未发生的修改。
只输出 JSON，格式如下：

{
  "title": "...",
  "body": "...",
  "commit_message": "...",
  "changed_files": [],
  "tests": "..."
}
""".strip()

        # 用户上下文单独放在 user_prompt，避免把 diff/test 内容混进 system prompt。
        user_prompt = f"""
Issue #{issue_number}

{issue_title}

Git Diff:

{diff}

Tests:

{test_summary}
""".strip()

        response = (
            self.client
            .chat
            .completions
            .create(
                model=settings.llm_model,
                temperature=0.1,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
            )
        )

        text = (
            response
            .choices[0]
            .message
            .content
            or ""
        )

        # parse_structured_output 会处理 ```json 代码块、前后废话和字段校验。
        return parse_structured_output(
            text,
            PullRequestDraft,
        )
