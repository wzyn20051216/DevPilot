"""! @brief 单智能体评测基线。

SingleDeveloperAgent 独立承担分析、修改和测试，用于和现有多智能体流水线
做消融实验。enable_rag 只控制 retrieve_code 是否可见，其余配置保持一致。
"""

from .base_tool_agent import BaseToolAgent


SINGLE_DEVELOPER_PROMPT = """
你是 DevPilot Single Developer Agent。

你独立负责完成整个软件开发任务：
1. 分析需求并定位相关代码；
2. 只修改完成任务所需的文件；
3. 检查 git diff；
4. 调用 run_test 运行真实测试；
5. 测试失败时继续定位和修复；
6. 测试通过后再结束任务。

不要声称测试成功，除非你真实调用了 run_test 并获得通过结果。
"""


class SingleDeveloperAgent(BaseToolAgent):
    """! @brief 同时负责开发和自测的单 Agent 对照组。"""

    def __init__(
        self,
        repo_path: str,
        enable_rag: bool,
        max_iterations: int = 12,
    ) -> None:
        """! @brief 初始化单 Agent 实验实例。

        @param repo_path 本次实验的独立 Workspace。
        @param enable_rag 是否允许调用 retrieve_code。
        @param max_iterations 最大 LLM 工具调用轮数。
        """

        tools = {
            "list_files",
            "read_file",
            "search_code",
            "write_file",
            "git_diff",
            "run_test",
            "run_command",
        }
        if enable_rag:
            tools.add("retrieve_code")

        super().__init__(
            repo_path=repo_path,
            name="coder",
            system_prompt=SINGLE_DEVELOPER_PROMPT,
            allowed_tools=tools,
            max_iterations=max_iterations,
        )
