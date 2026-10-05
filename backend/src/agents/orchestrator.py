'''调谁
什么时候调
失败后怎么办
谁给谁反馈
'''
from collections.abc import Callable, Generator, Iterator
from typing import Any

from ..models.agent_state import (
    AgentEvent,
    AgentState,
    PlanStep,
    ReviewerOutput,
    TesterOutput,
)
from ..services.structured_output import parse_structured_output
from ..services.structured_output import parse_protocol_output
from ..models.agent_protocol import (
    AgentHandoff, CoderProtocolOutput, PlannerProtocolOutput, ReviewerProtocolOutput,
)
from .code_agent import CodeAgent
from .planner_agent import PlannerAgent
from .reviewer_agent import ReviewerAgent
from .tester_agent import TesterAgent


def _consume_agent(
    agent,
    question: str,
):
    """跑完一个 agent 的整个流式过程，收集所有事件和最终结果。

    逐个消费 `agent.run_stream(question)` 产出的事件：
    - 把每个事件都收进 `events` 列表（调用方可再转发给前端）；
    - 顺带记住最后一个 `final` 事件的 `data`（也就是 agent 的最终产出）。

    Args:
        agent: 任意一个 BaseToolAgent 子类实例（planner/coder/tester/reviewer）。
        question: 传给该 agent 的问题。

    Returns:
        tuple[list[AgentEvent], dict | None]:
            第一个元素是完整的事件列表；
            第二个元素是 final 事件的 data（没有 final 则为 None）。
    """
    final_data = None

    events = []

    for event in agent.run_stream(question):
        events.append(event)

        if event.type == "final":
            final_data = event.data

    return events, final_data


class DevPilotOrchestrator:
    """多智能体编排器，串起 Planner → Coder → Tester → Reviewer 的完整流水线。

    职责（对应文件顶部那段说明）：
    - 调谁：按顺序调度 planner / coder / tester / reviewer 四个角色 agent；
    - 什么时候调：planner 先出计划，coder 按计划改代码，tester 验证，
      测试失败则在 `max_repair_rounds` 次内让 coder 返工并重测，最后 reviewer 审查；
    - 失败后怎么办：解析失败或测试不通过时 yield error / hand_off 事件，
      返工有上限保护，不会死循环；
    - 谁给谁反馈：tester 的失败报告回传给 coder，reviewer 独立审查 coder 的改动。
    """

    def __init__(
        self,
        repo_path: str,
        max_repair_rounds: int = 2,
        enable_rag: bool = True,
        cancel_check: Callable[[], bool] | None = None,
        checkpoint_callback: Callable[[str, list[dict[str, Any]]], None] | None = None,
        initial_contexts: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        """初始化编排器，并实例化四个角色 agent。

        Args:
            repo_path: 待分析/修改的代码仓库路径。
            max_repair_rounds: 测试或审查失败后共享的最大返工轮数，
                超过仍失败时终止任务，不进入下一阶段。
            enable_rag: 是否向 Planner/Coder/Reviewer 暴露 Hybrid RAG 工具。
            checkpoint_callback: 可选检查点钩子（断点恢复预埋）。回调签名
                (agent_name, messages)，由各 agent 在每轮迭代后调用；因为
                context_store 已按 (task_id, agent_name) 复合键落盘，这里
                一个回调即可覆盖全部四个角色。
            initial_contexts: 可选恢复上下文，key 为 agent 名（"planner"/
                "coder"/"tester"/"reviewer"），value 为待注入的起始消息列表。
        """
        self.repo_path = repo_path
        self.max_repair_rounds = max_repair_rounds
        self.enable_rag: bool = enable_rag
        self.cancel_check = cancel_check or (lambda: False)
        initial_contexts = initial_contexts or {}
        self.planner = PlannerAgent(
            repo_path,
            enable_rag=enable_rag,
            cancel_check=self.cancel_check,
            checkpoint_callback=checkpoint_callback,
            initial_messages=initial_contexts.get("planner"),
        )
        self.coder = CodeAgent(
            repo_path,
            enable_rag=enable_rag,
            cancel_check=self.cancel_check,
            checkpoint_callback=checkpoint_callback,
            initial_messages=initial_contexts.get("coder"),
        )
        self.tester = TesterAgent(
            repo_path,
            cancel_check=self.cancel_check,
            checkpoint_callback=checkpoint_callback,
            initial_messages=initial_contexts.get("tester"),
        )
        self.reviewer = ReviewerAgent(
            repo_path,
            enable_rag=enable_rag,
            cancel_check=self.cancel_check,
            checkpoint_callback=checkpoint_callback,
            initial_messages=initial_contexts.get("reviewer"),
        )

    def _run_role(self, agent, handoff: AgentHandoff, output_model) -> Generator[AgentEvent, None, Any]:
        """! @brief 流式执行并校验角色结果；任何错误/取消禁止继续向下游交接。"""
        output = None
        for event in agent.run_stream(handoff.model_dump_json()):
            yield event
            if event.type in {"error", "cancelled"}:
                return None
            if output is not None:
                yield AgentEvent(type="error", agent="orchestrator", message="角色 final 后仍产生事件",
                                 data={"failure_kind": "protocol_error", "phase": handoff.phase})
                return None
            if event.type == "final":
                try:
                    output = parse_protocol_output(event.message, output_model)
                except ValueError as exc:
                    yield AgentEvent(type="error", agent="orchestrator",
                                     message=f"{handoff.target} 交接报告不符合协议：{type(exc).__name__}",
                                     data={"failure_kind": "protocol_error", "phase": handoff.phase})
                    return None
        if output is None:
            yield AgentEvent(type="error", agent="orchestrator", message=f"{handoff.target} 未产出最终报告",
                             data={"failure_kind": "missing_role_output", "phase": handoff.phase})
        return output

    @staticmethod
    def _handoff_event(handoff: AgentHandoff) -> AgentEvent:
        """! @brief 对外保留原 plan 字段，并增加可验证的完整角色交接信封。"""
        return AgentEvent(type="hand_off", agent="orchestrator",
                          message=f"{handoff.source} → {handoff.target}（{handoff.phase}）",
                          data={"plan": [step.model_dump() for step in handoff.plan],
                                "handoff": handoff.model_dump(mode="json")})

    def _run_tester(self, question: str) -> tuple[list[AgentEvent], TesterOutput | None]:
        """
        跑一遍 tester，返回 (事件列表, TesterOutput)。
        事件列表用于 yield 给前端看实时过程；TesterOutput 是解析后的结果。
        解析失败时 TesterOutput 为 None，由调用方决定如何处理。
        """
        events: list[AgentEvent] = []
        self._last_test_execution_error = False
        self._last_tester_missing_run = True
        tester_answer = ""
        observed_report: TesterOutput | None = None
        saw_error = False  # 记录 tester 是否中途抛过 error（如达到最大迭代次数）
        for event in self.tester.run_stream(question):
            events.append(event)
            if event.type == "final":
                tester_answer = event.message
            elif event.type == "error":
                saw_error = True
                break
            elif event.type == "cancelled":
                return events, None
            elif event.type == "tool_result" and "test_report" in event.data:
                self._last_test_execution_error = bool(event.data.get("test_execution", {}).get("execution_error"))
                self._last_tester_missing_run = False
                # run_test 是独立执行器给出的机器结果，可信度高于
                # LLM 随后用自然语言重述的 JSON。先保留该事实作兜底。
                observed_report = TesterOutput.model_validate(
                    event.data["test_report"]
                )

        report: TesterOutput | None = None
        # 情况 1：tester 中途报错（例如达到 max_iterations），
        #         此时根本没有合法 final 产出，直接返回 None，
        #         由调用方根据 events 里的 error 事件判断真实原因。
        if saw_error and not tester_answer:
            return events, None

        # run_test 的机器结果是唯一可信的通过判据。LLM 的最终 JSON 只在
        # 没有机器结果时用于诊断，绝不能把真实失败覆盖成通过。
        if observed_report is not None:
            return events, observed_report

        # 没有观察到 run_test 时，仍解析最终输出用于展示，但上层不得把
        # 这种“只靠模型声明”的结果当成已验证成功。
        if tester_answer:
            try:
                report = parse_structured_output(
                    tester_answer,
                    TesterOutput,
                )
            except ValueError:
                report = None

        if report is not None:
            report.passed = False
            report.summary = (
                "Tester 未调用 run_test，不能确认测试通过。"
                + (f" 模型说明：{report.summary}" if report.summary else "")
            )
        return events, report

    def run_stream(self, question: str) -> Iterator[AgentEvent]:
        """! @brief Planner → Coder → Tester → Reviewer 的严格协议执行入口。"""
        state = AgentState(repo_path=self.repo_path, question=question, status="running", answer="")
        yield AgentEvent(type="start", agent="orchestrator", message="DevPilot 多智能体任务启动")
        incoming = AgentHandoff(source="user", target="planner", phase="planning", task=question)
        output = yield from self._run_role(self.planner, incoming, PlannerProtocolOutput)
        if output is None:
            return
        state.plan = [PlanStep.model_validate(step.model_dump()) for step in output.steps]
        yield from self._execute_plan(state, question)

    def execute_stream(self, question: str, plan: list[PlanStep]) -> Iterator[AgentEvent]:
        """! @brief 执行已批准计划；不重复调用 Planner，不修改历史审批内容。"""
        state = AgentState(repo_path=self.repo_path, question=question, status="running", answer="")
        state.plan = [step.model_copy(deep=True) for step in plan]
        yield AgentEvent(type="start", agent="orchestrator", message="DevPilot 多智能体任务启动（使用已批准计划）")
        yield from self._execute_plan(state, question)

    @staticmethod
    def _result_data(state: AgentState, coder_report: CoderProtocolOutput | None) -> dict[str, Any]:
        """! @brief 终态字段保持前端兼容，所有证据来自当前阶段已校验报告。"""
        return {
            "protocol_version": "1.0",
            "plan": [step.model_dump() for step in state.plan],
            "coder_report": coder_report.model_dump() if coder_report else None,
            "test_report": state.test_report.model_dump() if state.test_report else None,
            "review_report": state.review_report.model_dump() if state.review_report else None,
            "repair_rounds": state.repair_round,
        }

    def _execute_plan(self, state: AgentState, question: str) -> Iterator[AgentEvent]:
        """! @brief 统一初次实现、测试返工和审查返工；共享有界返工预算。"""
        coder_report = None
        source_role = "planner"
        phase = "implementation"
        while True:
            try:
                incoming = AgentHandoff(
                    source=source_role, target="coder", phase=phase, task=question,
                    plan=state.plan, coder_report=coder_report, test_report=state.test_report,
                    review_report=state.review_report, repair_round=state.repair_round,
                )
            except ValueError as exc:
                yield AgentEvent(type="error", agent="orchestrator",
                                 message=f"Coder 输入协议无效：{exc}",
                                 data={"failure_kind": "protocol_error", "phase": phase})
                return
            yield self._handoff_event(incoming)
            coder_report = yield from self._run_role(self.coder, incoming, CoderProtocolOutput)
            if coder_report is None:
                return
            state.modified_files = list(coder_report.modified_files)
            if coder_report.status == "blocked":
                yield AgentEvent(type="error", agent="orchestrator", message="Coder 报告任务阻塞",
                                 data={**self._result_data(state, coder_report), "failure_kind": "task_blocked"})
                return

            # 每次 Coder 执行后重测，不沿用旧的通过记录或旧审查批准。
            state.test_report = None
            state.review_report = None
            testing = AgentHandoff(source="coder", target="tester", phase="testing", task=question,
                                   plan=state.plan, coder_report=coder_report, repair_round=state.repair_round)
            yield self._handoff_event(testing)
            events, state.test_report = self._run_tester(testing.model_dump_json())
            for event in events:
                yield event
            if any(event.type in {"error", "cancelled"} for event in events):
                return
            if state.test_report is None or getattr(self, "_last_tester_missing_run", True):
                yield AgentEvent(type="error", agent="orchestrator", message="Tester 缺少有效机器验证报告",
                                 data={**self._result_data(state, coder_report), "failure_kind": "verification_missing"})
                return
            if not state.test_report.passed:
                # 收集/工具/超时错误没有可靠代码回归证据，不交给 Coder 盲目改实现。
                if getattr(self, "_last_test_execution_error", False):
                    yield AgentEvent(type="error", agent="orchestrator", message="测试执行未完成，需要检查环境或测试命令",
                                     data={**self._result_data(state, coder_report), "failure_kind": "test_execution_error"})
                    return
                if state.repair_round >= self.max_repair_rounds:
                    yield AgentEvent(type="error", agent="orchestrator", message="已达到最大返工次数，测试仍未通过",
                                     data={**self._result_data(state, coder_report), "failure_kind": "test_failure"})
                    return
                state.repair_round += 1
                source_role, phase = "tester", "repair"
                continue

            reviewing = AgentHandoff(source="tester", target="reviewer", phase="review", task=question,
                                     plan=state.plan, coder_report=coder_report, test_report=state.test_report,
                                     repair_round=state.repair_round)
            yield self._handoff_event(reviewing)
            reviewed = yield from self._run_role(self.reviewer, reviewing, ReviewerProtocolOutput)
            if reviewed is None:
                return
            state.review_report = ReviewerOutput.model_validate(reviewed.model_dump())
            if state.review_report.approved:
                state.status = "completed"
                yield AgentEvent(type="final", agent="orchestrator",
                                 message="DevPilot 已完成开发任务，真实测试与代码审查均通过。",
                                 data=self._result_data(state, coder_report))
                return
            if state.repair_round >= self.max_repair_rounds:
                yield AgentEvent(type="error", agent="orchestrator", message="Reviewer 未批准修改，返工预算已用尽",
                                 data={**self._result_data(state, coder_report), "failure_kind": "review_rejected"})
                return
            state.repair_round += 1
            source_role, phase = "reviewer", "repair"

    def plan(self, question: str) -> list[PlanStep]:
        """! @brief 只生成经严格协议校验的计划，供 API 展示和用户批准。"""
        incoming = AgentHandoff(source="user", target="planner", phase="planning", task=question)
        events = self._run_role(self.planner, incoming, PlannerProtocolOutput)
        while True:
            try:
                next(events)
            except StopIteration as finished:
                output = finished.value
                break
        if output is None:
            raise ValueError("Planner 未产出有效协议报告，不能生成可审批计划")
        return [PlanStep.model_validate(step.model_dump()) for step in output.steps]
