from collections.abc import Callable
from typing import Any

from openai.types.chat import ChatCompletionMessageParam

from .base_tool_agent import BaseToolAgent
from ..models.agent_protocol import TesterProtocolOutput

TESTER_PROMPT = """
你是 DevPilot 的 Tester Agent。

你的职责是验证 Coder 的修改是否正确。

要求：
1. 先用 git_diff 查看改动文件，结合原始任务选择直接相关的测试目标；
   优先调用 run_test(target=目标文件或测试节点)，不要一开始就运行整个旧仓库。
   无法定位目标时才运行全套测试。
2. 必要时读取失败相关代码。
3. 可以运行允许的静态检查工具。
4. 你不能修改文件。
5. 根据真实测试结果判断通过或失败。
6. run_test 返回后应立即总结；只有测试失败时才继续读取相关代码。
7. 失败反馈必须说明测试目标、命令/退出码、关键失败及其与修改的关系。
   区分代码失败、未收集测试、超时、缺依赖和只读/禁网环境问题；
   无关环境故障单独记录，不能猜测它已解决，也不能忽略未知代码失败。
8. passed、stdout、stderr 必须对应最后一次真实 run_test，不能用分析替代测试。
   summary 明确已验证范围，目标测试通过不能宣称整个仓库测试通过。

最终回答必须只输出 JSON：

{
  "passed": true,
  "summary": "测试结论",
  "stdout": "关键测试输出",
  "stderr": "关键错误信息"
}

即使 stdout/stderr 包含换行，也必须转义为合法 JSON 字符串。
输出完整 JSON 后立即结束，不得再调用工具或追加解释。
"""


class TesterAgent(BaseToolAgent):
    """Tester 角色：运行测试验证 Coder 的修改是否正确（只读，不改代码）。"""

    def __init__(
        self,
        repo_path: str,
        cancel_check: Callable[[], bool] | None = None,
        checkpoint_callback: Callable[[str, list[dict[str, Any]]], None] | None = None,
        initial_messages: list[ChatCompletionMessageParam] | None = None,
    ) -> None:
        """初始化 Tester agent。

        Args:
            repo_path: 待测试的代码仓库路径。
            checkpoint_callback: 可选检查点钩子（断点恢复预埋）。
            initial_messages: 可选起始消息（断点恢复预埋）。
        """
        super().__init__(
            repo_path=repo_path,
            name="tester",
            system_prompt=TESTER_PROMPT,
            allowed_tools={
                "git_diff",
                "read_file",
                "search_code",
                "run_test",
                "run_command",
            },
            max_iterations=5,
            cancel_check=cancel_check,
            checkpoint_callback=checkpoint_callback,
            initial_messages=initial_messages,
            output_model=TesterProtocolOutput,
        )
