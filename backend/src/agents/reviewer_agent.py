from collections.abc import Callable
from typing import Any

from openai.types.chat import ChatCompletionMessageParam

from .base_tool_agent import BaseToolAgent

REVIEWER_PROMPT = """
你是 DevPilot 的 Reviewer Agent。

你的职责是独立审查 Coder 的修改。

重点检查：
1. 是否真正满足用户需求。
2. 是否引入明显逻辑错误。
3. 是否存在不必要修改。
4. 是否遗漏边界情况。
5. Git Diff 是否清晰合理。
6. 不要直接修改代码。

最终回答必须只输出 JSON：

{
  "approved": true,
  "summary": "审查结论",
  "issues": []
}

issues 必须是字符串数组，不要输出 severity/file/description 对象。
即使没有问题也必须输出完整 JSON，不得再调用工具或追加解释。
"""


class ReviewerAgent(BaseToolAgent):
    """Reviewer 角色：独立审查 Coder 的改动是否满足需求（只读，不改代码）。"""

    def __init__(
        self,
        repo_path: str,
        enable_rag: bool = True,
        cancel_check: Callable[[], bool] | None = None,
        checkpoint_callback: Callable[[str, list[dict[str, Any]]], None] | None = None,
        initial_messages: list[ChatCompletionMessageParam] | None = None,
    ) -> None:
        """初始化 Reviewer agent。

        Args:
            repo_path: 待审查的代码仓库路径。
            enable_rag: 是否向 Reviewer 暴露 Hybrid RAG 检索工具。
            checkpoint_callback: 可选检查点钩子（断点恢复预埋）。
            initial_messages: 可选起始消息（断点恢复预埋）。
        """
        tools = {
            "read_file",
            "search_code",
            "git_diff",
        }
        if enable_rag:
            tools.add("retrieve_code")

        super().__init__(
            repo_path=repo_path,
            name="reviewer",
            system_prompt=REVIEWER_PROMPT,
            allowed_tools=tools,
            max_iterations=5,
            cancel_check=cancel_check,
            checkpoint_callback=checkpoint_callback,
            initial_messages=initial_messages,
        )
