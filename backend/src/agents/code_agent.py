from collections.abc import Callable
from typing import Any

from openai.types.chat import ChatCompletionMessageParam

from .base_tool_agent import BaseToolAgent


CODER_PROMPT = """
你是 DevPilot 的 Coder Agent。

你的职责是根据用户任务和 Planner 给出的计划，
阅读、修改并检查代码。

要求：
1. 修改代码前先阅读相关文件。
2. 只修改完成任务必要的文件。
3. 禁止修改 .env 等敏感文件。
4. 修改后必须查看 git_diff。
5. 不负责最终代码审查。
6. 不要声称测试通过，测试由 Tester Agent 完成。
7. 大文件先 search_code 定位，再用 read_file 的 start_line/end_line 读取局部，
   优先用 replace_in_file 做唯一锚点替换，避免重写整个文件。
8. 修复迭代器/字符串/序列化等运行时协议问题时，先用 protocol_probe 工具在沙箱
   观察真实行为，修复后重跑同一探针确认。
"""


class CodeAgent(BaseToolAgent):
    """Coder 角色：负责阅读、修改、检查代码（不负责测试和最终审查）。"""

    def __init__(
        self,
        repo_path: str,
        max_iterations: int = 8,
        enable_rag: bool = True,
        cancel_check: Callable[[], bool] | None = None,
        checkpoint_callback: Callable[[str, list[dict[str, Any]]], None] | None = None,
        initial_messages: list[ChatCompletionMessageParam] | None = None,
    ) -> None:
        """初始化 Coder agent。

        Args:
            repo_path: 待操作的代码仓库路径。
            max_iterations: 单次任务的 tool-calling 最大迭代轮数。
            enable_rag: 是否向 Coder 暴露 Hybrid RAG 检索工具。
            checkpoint_callback: 可选检查点钩子（断点恢复预埋）。
            initial_messages: 可选起始消息（断点恢复预埋）。
        """
        tools = {
            "list_files",
            "read_file",
            "search_code",
            "write_file",
            "replace_in_file",
            "git_diff",
            "protocol_probe",
        }
        if enable_rag:
            tools.add("retrieve_code")

        # 角色专属配置在子类里定死，对外只暴露 repo_path / max_iterations，
        # 和 PlannerAgent / TesterAgent / ReviewerAgent 保持一致。
        super().__init__(
            repo_path=repo_path,
            name="coder",
            system_prompt=CODER_PROMPT,
            allowed_tools=tools,
            max_iterations=max_iterations,
            edit_deadline=max(1, max_iterations - 3),
            cancel_check=cancel_check,
            checkpoint_callback=checkpoint_callback,
            initial_messages=initial_messages,
        )
