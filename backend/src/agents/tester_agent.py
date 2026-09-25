from .base_tool_agent import BaseToolAgent


TESTER_PROMPT = """
你是 DevPilot 的 Tester Agent。

你的职责是验证 Coder 的修改是否正确。

要求：
1. 优先运行 run_test（注意是单数 run_test，不是 run_tests）。
2. 必要时读取失败相关代码。
3. 可以运行允许的静态检查工具。
4. 你不能修改文件。
5. 根据真实测试结果判断通过或失败。

最终回答必须只输出 JSON：

{
  "passed": true,
  "summary": "测试结论",
  "stdout": "关键测试输出",
  "stderr": "关键错误信息"
}
"""


class TesterAgent(BaseToolAgent):
    """Tester 角色：运行测试验证 Coder 的修改是否正确（只读，不改代码）。"""

    def __init__(
        self,
        repo_path: str,
    ) -> None:
        """初始化 Tester agent。

        Args:
            repo_path: 待测试的代码仓库路径。
        """
        super().__init__(
            repo_path=repo_path,
            name="tester",
            system_prompt=TESTER_PROMPT,
            allowed_tools={
                "read_file",
                "search_code",
                "run_test",
                "run_command",
            },
            max_iterations=5,
        )
