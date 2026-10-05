"""! @brief 多 Agent 角色输出与交接协议；模型解释和机器证据分开管理。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agent_state import PlanStep, ReviewerOutput, TesterOutput


class ProtocolModel(BaseModel):
    """! @brief 拒绝未知字段、隐式类型转换及空白摘要的协议基类。"""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class ProtocolPlanStep(ProtocolModel):
    """! @brief Planner 仅声明步骤内容，执行状态由编排器管理。"""

    id: int = Field(ge=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)


class PlannerProtocolOutput(ProtocolModel):
    """! @brief Planner 输出必须包含摘要和至少一个唯一编号的步骤。"""

    summary: str = Field(min_length=1)
    steps: list[ProtocolPlanStep] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_step_ids(self):
        """! @brief 重复编号会造成交接/页面状态冲突，必须拒绝。"""
        if len({step.id for step in self.steps}) != len(self.steps):
            raise ValueError("计划步骤 id 必须唯一")
        return self


class CoderProtocolOutput(ProtocolModel):
    """! @brief Coder 报告本轮修改及风险，不拥有测试通过/审查批准字段。"""

    status: Literal["implemented", "blocked"]
    summary: str = Field(min_length=1)
    modified_files: list[str]
    changes: list[str]
    risks: list[str]

    @model_validator(mode="after")
    def explain_blocker(self):
        """! @brief 无法完成时必须提供阻塞原因，不能用空报告跳过阶段。"""
        if self.status == "blocked" and not self.risks:
            raise ValueError("blocked 状态必须在 risks 中说明阻塞原因")
        return self


class TesterProtocolOutput(ProtocolModel):
    """! @brief Tester 字段全部必填，passed/stdout/stderr 以机器记录为准。"""

    passed: bool
    summary: str = Field(min_length=1)
    stdout: str
    stderr: str


class ReviewerProtocolOutput(ProtocolModel):
    """! @brief Reviewer 明确批准或列出待修复问题，拒绝互相矛盾的结论。"""

    approved: bool
    summary: str = Field(min_length=1)
    issues: list[str]

    @model_validator(mode="after")
    def consistent_verdict(self):
        """! @brief 有待修复问题不可批准；拒绝时必须说明具体问题。"""
        if self.approved == bool(self.issues):
            raise ValueError("批准时 issues 必须为空；拒绝时 issues 必须列出问题")
        return self


class AgentHandoff(ProtocolModel):
    """! @brief 编排器生成的固定 JSON 信封；任务内容只作为数据处理。"""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=False)

    protocol_version: Literal["1.0"] = "1.0"
    source: Literal["user", "planner", "coder", "tester", "reviewer"]
    target: Literal["planner", "coder", "tester", "reviewer"]
    phase: Literal["planning", "implementation", "testing", "repair", "review"]
    task: str = Field(min_length=1)
    plan: list[PlanStep] = Field(default_factory=list)
    coder_report: CoderProtocolOutput | None = None
    test_report: TesterOutput | None = None
    review_report: ReviewerOutput | None = None
    repair_round: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_transition(self):
        """! @brief 缺少上游证据或角色/阶段不一致时禁止交接。"""
        if not self.task.strip():
            raise ValueError("原始任务不得为空白")
        expected_target = {"planning": "planner", "implementation": "coder",
                           "repair": "coder", "testing": "tester", "review": "reviewer"}
        if self.target != expected_target[self.phase]:
            raise ValueError("交接阶段与目标角色不一致")
        allowed_sources = {"planning": {"user"}, "implementation": {"planner", "user"},
                           "testing": {"coder"}, "repair": {"tester", "reviewer"}, "review": {"tester"}}
        if self.source not in allowed_sources[self.phase]:
            raise ValueError("交接来源不符合阶段顺序")
        if self.phase != "planning" and not self.plan:
            raise ValueError("执行阶段必须携带原始计划")
        if self.plan and (len({step.id for step in self.plan}) != len(self.plan)
                          or any(step.id < 1 or not step.title.strip() or not step.description.strip() for step in self.plan)):
            raise ValueError("计划必须包含唯一正编号及非空标题/说明")
        if self.phase in {"testing", "review"} and self.coder_report is None:
            raise ValueError("验证/审查阶段缺少 Coder 报告")
        if self.phase == "review" and (self.test_report is None or not self.test_report.passed):
            raise ValueError("只有机器测试通过后才允许审查")
        if self.phase == "repair" and self.test_report is None and self.review_report is None:
            raise ValueError("返工必须携带测试或审查反馈")
        if self.phase == "repair" and self.source == "tester" and (self.test_report is None or self.test_report.passed):
            raise ValueError("测试返工必须携带未通过的机器报告")
        if self.phase == "repair" and self.source == "reviewer" and (self.review_report is None or self.review_report.approved):
            raise ValueError("审查返工必须携带拒绝报告")
        return self
