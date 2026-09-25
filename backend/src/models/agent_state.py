"""记录 DevPilot Agent 的运行状态和工具调用历史。

本模块用一组 Pydantic 模型描述两个层面的事：

1. **State（状态快照）** —— AgentState 描述"整体运行状态"，
   包括当前迭代轮次、工具调用历史、已修改文件、测试/审查报告、最终回答等。
   类比 RPG 角色面板：生命值 80、等级 5、位置北京。

2. **Event（事件）** —— AgentEvent 描述"运行过程中刚刚发生了什么"，
   例如开始运行、思考中、工具调用、工具结果、最终结果、错误等。
   类比战斗日志：刚刚受到 20 点伤害。

简单说：State 是"现在整体什么样"，Event 是"刚刚发生了什么"，
两者配合起来就能还原 agent 的一次完整执行过程。
"""
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

AgentStatus = Literal[
    "pending",      # 尚未开始
    "running",      # 正在执行
    "completed",    # 已完成
    "failed",       # 失败
]


AgentEventType = Literal[
    "start",        # 任务开始
    "thinking",     # agent 正在思考（某一轮迭代开始）
    "tool_call",    # 即将调用某个工具
    "tool_result",  # 工具执行完毕
    "hand_off",     # 多智能体之间交接（如 planner→coder）
    "plan",         # 计划产出
    "review",       # 审查产出
    "test_result",  # 测试结果产出
    "final",        # 最终结果
    "error",        # 出错
]


AgentRole = Literal[
    "orchestrator",  # 编排器，调度其它角色
    "planner",       # 计划者，出开发计划
    "coder",         # 编码者，改代码
    "tester",        # 测试者，跑测试验证
    "reviewer",      # 审查者，独立 review
]
class PlanStep(BaseModel):
    """Planner 生成的单条开发步骤。"""

    id: int
    title: str
    description: str
    # 步骤自身的执行状态，默认 pending（未开始）
    status: Literal["pending", "running", "completed", "failed"] = "pending"


class PlannerOutput(BaseModel):
    """! @brief Planner Agent 的结构化输出模型。

    Planner 最终回答必须包含任务摘要和可执行步骤列表。
    Orchestrator 会把 LLM 返回的 JSON 解析成该模型，再取 steps 写入 AgentState。
    """

    summary: str = Field(
        default="",
        description="Planner 对任务的总体说明",
    )
    steps: list[PlanStep] = Field(
        min_length=1,
        description="Planner 生成的开发步骤列表，至少包含一个步骤",
    )


class TesterOutput(BaseModel):
    """! @brief Tester Agent 的结构化输出模型。

    Tester 最终回答必须给出测试是否通过、测试结论以及关键 stdout/stderr。
    Orchestrator 会把 LLM 返回的 JSON 解析成该模型，再写入 AgentState。
    """

    passed: bool
    summary: str = ""      # 测试结论摘要
    stdout: str = ""       # 关键测试输出
    stderr: str = ""       # 关键错误信息


class ReviewerOutput(BaseModel):
    """! @brief Reviewer Agent 的结构化输出模型。

    Reviewer 最终回答必须给出是否批准、审查摘要和问题列表。
    Orchestrator 会把 LLM 返回的 JSON 解析成该模型，再写入 AgentState。
    """

    approved: bool
    summary: str = ""      # 审查结论
    # 对外始终保持 list[str]，让 API 和前端不需要处理多种结构。
    issues: list[str] = Field(default_factory=list)

    @field_validator("issues", mode="before")
    @classmethod
    def normalize_issues(cls, value: Any) -> list[str]:
        """! @brief 兼容 LLM 常输出的对象形式问题列表。

        Prompt 约定 ``issues`` 为字符串数组，但模型有时会输出
        ``{"severity": ..., "description": ...}`` 对象。这种表达包含更多
        信息，不应让整条编排流程失败；因此在模型边界统一序列化。
        """

        if value is None:
            return []
        if isinstance(value, (str, dict)):
            value = [value]
        if not isinstance(value, list):
            value = [value]

        normalized: list[str] = []
        for issue in value:
            if isinstance(issue, str):
                normalized.append(issue)
            elif isinstance(issue, dict):
                normalized.append(
                    json.dumps(issue, ensure_ascii=False, default=str)
                )
            else:
                normalized.append(str(issue))
        return normalized


# 兼容旧代码里的命名。新代码优先使用 TesterOutput / ReviewerOutput。
TestReport = TesterOutput
ReviewReport = ReviewerOutput


class ToolCallRecord(BaseModel):
    """单次工具调用的历史记录。"""

    iteration: int = Field(..., description="发生在第几轮迭代")
    tool: str = Field(..., description="工具名称")
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="工具调用参数",
    )
    # 结果预览字符串，默认空串（而不是 None），
    # 保证序列化/展示时不会出现 NoneType 错误。
    result_preview: str = ""

class AgentState(BaseModel):
    """DevPilot Agent 当前运行状态的完整快照。"""

    repo_path: str = Field(..., description="代码仓库路径")
    question: str = Field(..., description="用户提出的问题")
    status: Annotated[
        AgentStatus,
        Field(description="Agent 当前状态"),
    ] = "pending"
    tool_calls: list[ToolCallRecord] = Field(
        default_factory=list,
        description="工具调用历史记录",
    )
    answer: str = Field("", description="LLM 的回答")
    iteration: int = Field(default=0, description="Agent 当前迭代轮次")
    plan: list[PlanStep] = Field(default_factory=list, description="Planner 制定的计划")
    modified_files: list[str] = Field(
        default_factory=list,
        description="Agent 已修改的文件（write_file 工具记录）",
    )
    test_report: TesterOutput | None = None     # Tester 的测试报告，未跑测试时为 None
    review_report: ReviewerOutput | None = None # Reviewer 的审查报告，未审查时为 None
    repair_round: int = 0                        # 返工次数（测试失败后 coder 重修的轮数）
    # Token 用量在每次 LLM 响应后累加。供应商不返回 usage 时保持 0，
    # 评测层仍可正常运行，只是该次实验没有成本数据。
    prompt_tokens: int = Field(default=0, ge=0, description="累计输入 Token 数")
    completion_tokens: int = Field(default=0, ge=0, description="累计输出 Token 数")
    total_tokens: int = Field(default=0, ge=0, description="累计总 Token 数")
    # 注意：tests_passed 已被 test_report.passed 取代，此处保留仅为兼容旧引用，
    # 但未跑测试就默认 True 属于危险设计，新代码请改用 test_report。
    tests_passed: bool = True


class AgentEvent(BaseModel):
    """Agent 执行过程中产生的事件。"""

    type: AgentEventType
    agent: AgentRole = "orchestrator"            # 事件由哪个角色产生
    iteration: int = Field(default=0, description="事件发生时的迭代轮次")
    message: str = Field("", description="事件消息内容")
    data: dict[str, Any] = Field(default_factory=dict, description="事件附带的结构化数据")
