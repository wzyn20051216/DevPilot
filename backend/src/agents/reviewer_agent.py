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
"""


class ReviewerAgent(BaseToolAgent):
    """Reviewer 角色：独立审查 Coder 的改动是否满足需求（只读，不改代码）。"""

    def __init__(
        self,
        repo_path: str,
        enable_rag: bool = True,
    ) -> None:
        """初始化 Reviewer agent。

        Args:
            repo_path: 待审查的代码仓库路径。
            enable_rag: 是否向 Reviewer 暴露 Hybrid RAG 检索工具。
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
        )
