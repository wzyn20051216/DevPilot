"""! @brief 单智能体评测基线。

SingleDeveloperAgent 独立承担分析、修改和测试，用于和现有多智能体流水线
做消融实验。enable_rag 只控制 retrieve_code 是否可见，其余配置保持一致。
"""

from collections.abc import Callable
from typing import Any

from openai.types.chat import ChatCompletionMessageParam

from .base_tool_agent import BaseToolAgent
from .strategy import AgentStrategy


SINGLE_DEVELOPER_CORE_PROMPT = """
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
"""

SINGLE_DEVELOPER_PROBE_PROMPT = """
修复属性错误、字符串/序列化/类型或数据模型协议前，先执行「运行时验证协议」
（用 protocol_probe 在沙箱观察真实行为，不凭名称猜约定）：
1. Issue 给了复现代码/输入/异常必须原样运行该场景，不改名、不换输入、不补初始化。
2. 迭代器/生成器修复必须探针覆盖：正常产出、空迭代、迭代中抛业务异常、
   __iter__/__next__ 成套、重复迭代、旧式 next() 兼容；next(g, default) 只吞 StopIteration。
3. 字符串/序列化/类型行为先探针观察 str/repr/属性再改；语义沿对象关系传播
   （先查 root/owner/parent 等解析机制），不要 getattr(..., None) 吞异常丢配置。
4. 修复后重跑同一探针确认，再进入 run_test。
"""

# 历史基线仍由原文的两段逐字拼接，用于复现 single_no_rag/single_enhanced。
SINGLE_DEVELOPER_PROMPT = SINGLE_DEVELOPER_CORE_PROMPT + SINGLE_DEVELOPER_PROBE_PROMPT

# 增强策略（single_enhanced 变体）在基线提示词之后追加，基线保持逐字不变，
# 保证 A/B 对照只改变这里列出的因素。各条依据见 docs/capability-improvement-plan.md。
ENHANCED_ADDENDUM = """
增强工作流（在上面规则之外额外执行）：
A. 结构导航：定位到 Python 文件后，先用 code_outline 看类/方法签名与行号骨架，
   再用 code_outline(symbol="Class.method") 精确读取定义，少用大段 read_file。
B. 修改前先找该模块已有测试和同类实现，遵循仓库既有约定（异常类型、返回值、命名），
   维护者写的隐藏测试通常按这些约定检查行为。
C. 迭代器/容器协议探针逐项列出：list(obj)、空对象、iter(obj) 返回值、
   直接 next(obj)（未先 iter 时的行为）、next(it, default)、重复迭代、in 运算。
D. 修复后思考 Issue 未明说的边界（None、空值、类型混用、子类），必要时补一条探针。
E. 编辑若因语法错误被拒绝，文件保持原样：按返回的行号修正后重新提交。
"""

# 动态路由不改动上面两个历史提示词常量，以保证既有 A/B 实验可复现。
# 只在显式传入 AgentStrategy 时按决策追加对应的独立段落。
STRATEGY_OUTLINE_ADDENDUM = """
本轮启用结构导航：定位到 Python 文件后，先用 code_outline 查看类、
方法签名与行号骨架，再按符号精确读取定义，避免大段 read_file。
"""

STRATEGY_PROBE_ADDENDUM = """
本轮强制先探针后编辑：修改前必须用 protocol_probe 运行 Issue 中的真实输入，
记录当前行为；修改后重跑同一探针，确认行为变化后才进入 run_test。
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
        enhanced: bool = False,
        strategy: AgentStrategy | None = None,
    ) -> None:
        """! @brief 初始化单 Agent 实验实例。

        @param repo_path 本次实验的独立 Workspace。
        @param enable_rag 是否允许调用 retrieve_code。
        @param max_iterations 最大 LLM 工具调用轮数。
        @param checkpoint_callback 可选检查点钩子（断点恢复预埋）。
        @param initial_messages 可选起始消息（断点恢复预埋）。
        @param enhanced 是否启用增强工作流与 code_outline 工具。
        @param strategy 可选的动态策略；None 时保持既有行为逐字不变。
        """

        # 任务策略仍返回可观测的 static_fallback 决策，但 Agent 组装时
        # 必须把它视为 None，才能保持历史基线逐字不变。
        if strategy is not None and strategy.mode == "static_fallback":
            strategy = None

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
        }
        if enable_rag:
            tools.add("retrieve_code")
        use_outline = strategy.use_outline if strategy is not None else enhanced
        if use_outline:
            tools.add("code_outline")

        if strategy is None:
            system_prompt = (
                SINGLE_DEVELOPER_PROMPT + ENHANCED_ADDENDUM
                if enhanced
                else SINGLE_DEVELOPER_PROMPT
            )
            effective_max_iterations = max_iterations
        else:
            additions = []
            if strategy.use_outline:
                additions.append(STRATEGY_OUTLINE_ADDENDUM)
            if strategy.enforce_probe:
                additions.extend(
                    [SINGLE_DEVELOPER_PROBE_PROMPT, STRATEGY_PROBE_ADDENDUM]
                )
            system_prompt = SINGLE_DEVELOPER_CORE_PROMPT + "".join(additions)
            effective_max_iterations = strategy.max_iterations

        super().__init__(
            repo_path=repo_path,
            name="coder",
            system_prompt=system_prompt,
            allowed_tools=tools,
            max_iterations=effective_max_iterations,
            edit_deadline=9,
            cancel_check=cancel_check,
            checkpoint_callback=checkpoint_callback,
            initial_messages=initial_messages,
        )
