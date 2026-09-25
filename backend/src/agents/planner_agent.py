from .base_tool_agent import BaseToolAgent


PLANNER_PROMPT = """
你是 DevPilot 的 Planner Agent。

你的职责是：
1. 理解用户的软件开发任务。
2. 必要时读取代码仓库。
3. 找到与任务最相关的文件。
4. 制定具体、可执行的开发计划。

你不能修改任何代码。
对于小型仓库，可以使用 list_files / read_file。

对于代码位置不明确、跨多个模块或仓库较大的任务，
优先使用 retrieve_code 进行语义检索。

retrieve_code 返回的是候选上下文，
必要时仍应使用 read_file 查看完整文件后再下结论。

最终回答必须只输出 JSON：

{
  "summary": "任务总体说明",
  "steps": [
    {
      "id": 1,
      "title": "步骤标题",
      "description": "具体要做什么"
    }
  ]
}
"""


class PlannerAgent(BaseToolAgent):
    """Planner 角色：理解任务、读代码、产出开发计划（只读，不改代码）。"""

    def __init__(
        self,
        repo_path: str,
        enable_rag: bool = True,
    ) -> None:
        """初始化 Planner agent。

        Args:
            repo_path: 待分析的代码仓库路径。
            enable_rag: 是否向 Planner 暴露 Hybrid RAG 检索工具。
        """
        tools = {
            "list_files",
            "read_file",
            "search_code",
        }
        if enable_rag:
            tools.add("retrieve_code")

        super().__init__(
            repo_path=repo_path,
            name="planner",
            system_prompt=PLANNER_PROMPT,
            allowed_tools=tools,
            max_iterations=10,
        )
