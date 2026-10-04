"""! @brief 单智能体评测基线。

SingleDeveloperAgent 独立承担分析、修改和测试，用于和现有多智能体流水线
做消融实验。enable_rag 只控制 retrieve_code 是否可见，其余配置保持一致。
"""

from collections.abc import Callable
from typing import Any

from openai.types.chat import ChatCompletionMessageParam

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

真实仓库文件可能很大：先用 search_code 定位行号，再用 read_file 的
start_line/end_line 读取局部上下文，并优先用 replace_in_file 做唯一锚点替换。
不要为了一个局部修改重写整个大文件，也不要重复读取相同内容。

不要声称测试成功，除非你真实调用了 run_test 并获得通过结果。

你最多有 14 轮工具交互。按以下预算执行：
- 前 6 轮完成定位；找到能解释现象的最小修复后立即修改，不做穷尽式搜索；
- 第 9 轮前必须至少调用一次 replace_in_file 或 write_file；
- 保留最后 5 轮运行测试、查看 diff，并根据真实失败继续修复。
若信息已经足够，提前修改和测试，不要为了写长篇分析耗尽工具轮次。

修复属性错误、字符串/序列化/类型或数据模型协议前，先执行「运行时验证协议」
（用 protocol_probe 在沙箱观察真实行为，不凭名称猜约定）：
1. Issue 给了复现代码/输入/异常必须原样运行该场景，不改名、不换输入、不补初始化。
2. **迭代器/生成器修复的完整探针清单**（缺一不可）：
   a) 正常迭代产出: `list(obj)` 或 `for x in obj`
   b) 空迭代场景: 确认空对象迭代不抛异常
   c) 迭代中业务异常: 验证异常正确传播
   d) __iter__/__next__ 成套: 确认 `iter(obj)` 返回迭代器
   e) 重复迭代: 多次调用 `list(obj)` 验证可重复性
   f) **旧式 next() 兼容**: 显式验证 `next(obj)` 和 `next(obj, default)` 工作
   g) StopIteration 正确性: 确认 `next(g, default)` 只吞 StopIteration
3. 字符串/序列化/类型行为先探针观察 str/repr/属性再改；语义沿对象关系传播
   （先查 root/owner/parent 等解析机制），不要 getattr(..., None) 吞异常丢配置。
4. **探针自检**: 构造探针前，对照上述清单确认是否覆盖了所有协议点。
5. 修复后重跑同一探针确认，再进入 run_test。
"""


class SingleDeveloperAgent(BaseToolAgent):
    """! @brief 同时负责开发和自测的单 Agent 对照组。"""

    def __init__(
        self,
        repo_path: str,
        enable_rag: bool,
        max_iterations: int = 14,
        cancel_check: Callable[[], bool] | None = None,
        checkpoint_callback: Callable[[str, list[dict[str, Any]]], None] | None = None,
        initial_messages: list[ChatCompletionMessageParam] | None = None,
    ) -> None:
        """! @brief 初始化单 Agent 实验实例。

        @param repo_path 本次实验的独立 Workspace。
        @param enable_rag 是否允许调用 retrieve_code。
        @param max_iterations 最大 LLM 工具调用轮数。
        @param checkpoint_callback 可选检查点钩子（断点恢复预埋）。
        @param initial_messages 可选起始消息（断点恢复预埋）。
        """

        tools = {
            "list_files",
            "read_file",
            "search_code",
            "write_file",
            "replace_in_file",
            "git_diff",
            "run_test",
            "run_command",
            "protocol_probe",
            "analyze_repository_context",
            "analyze_code_semantics",
        }
        if enable_rag:
            tools.add("retrieve_code")

        super().__init__(
            repo_path=repo_path,
            name="coder",
            system_prompt=SINGLE_DEVELOPER_PROMPT,
            allowed_tools=tools,
            max_iterations=max_iterations,
            edit_deadline=9,
            cancel_check=cancel_check,
            checkpoint_callback=checkpoint_callback,
            initial_messages=initial_messages,
        )
