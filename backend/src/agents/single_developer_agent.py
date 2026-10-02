"""! @brief 单智能体评测基线。

SingleDeveloperAgent 独立承担分析、修改和测试，用于和现有多智能体流水线
做消融实验。enable_rag 只控制 retrieve_code 是否可见，其余配置保持一致。
"""

from collections.abc import Callable

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

修复包装器、容器或代理对象引起的属性错误时，要保持原有语义沿对象关系传播。
先检查项目是否已有 root、owner、parent 等解析真实所属对象的机制；不要只用
getattr(..., None) 吞掉异常并回退默认值，因为这可能让基础测试通过却丢失配置。
当修复字符串表示、序列化、类型或运行时协议时，优先用 run_command 的
python -c 在隔离沙箱观察相关对象已有的 str/repr/属性行为，再决定返回值；
不要凭名称猜测项目约定。
实现 Python 数据模型协议时要检查协议是否需要成套方法以及兼容行为，例如
可迭代对象与迭代器的 __iter__/__next__ 区别、成员判断、重复迭代和异常类型；
用 python -c 覆盖这些边界，避免只验证最常见路径。
"""


class SingleDeveloperAgent(BaseToolAgent):
    """! @brief 同时负责开发和自测的单 Agent 对照组。"""

    def __init__(
        self,
        repo_path: str,
        enable_rag: bool,
        max_iterations: int = 14,
        cancel_check: Callable[[], bool] | None = None,
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
            "replace_in_file",
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
            edit_deadline=9,
            cancel_check=cancel_check,
        )
