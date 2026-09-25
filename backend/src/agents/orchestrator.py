'''调谁
什么时候调
失败后怎么办
谁给谁反馈
'''
import json
from collections.abc import Iterator

from .code_agent import CodeAgent
from .planner_agent import PlannerAgent
from .reviewer_agent import ReviewerAgent
from .tester_agent import TesterAgent
from ..models.agent_state import AgentEvent, AgentState, PlannerOutput, PlanStep, ReviewerOutput, TesterOutput
from ..services.structured_output import parse_structured_output


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
    ) -> None:
        """初始化编排器，并实例化四个角色 agent。

        Args:
            repo_path: 待分析/修改的代码仓库路径。
            max_repair_rounds: 测试失败后允许 coder 返工的最大轮数，
                超过仍失败则直接进入 reviewer（保护性上限，避免死循环）。
            enable_rag: 是否向 Planner/Coder/Reviewer 暴露 Hybrid RAG 工具。
        """
        self.repo_path = repo_path
        self.max_repair_rounds = max_repair_rounds
        self.enable_rag: bool = enable_rag
        self.planner = PlannerAgent(repo_path, enable_rag=enable_rag)
        self.coder = CodeAgent(repo_path, enable_rag=enable_rag)
        self.tester = TesterAgent(repo_path)
        self.reviewer = ReviewerAgent(repo_path, enable_rag=enable_rag)

    def _run_tester(self, question: str) -> tuple[list[AgentEvent], TesterOutput | None]:
        """
        跑一遍 tester，返回 (事件列表, TesterOutput)。
        事件列表用于 yield 给前端看实时过程；TesterOutput 是解析后的结果。
        解析失败时 TesterOutput 为 None，由调用方决定如何处理。
        """
        events: list[AgentEvent] = []
        tester_answer = ""
        saw_error = False  # 记录 tester 是否中途抛过 error（如达到最大迭代次数）
        for event in self.tester.run_stream(question):
            events.append(event)
            if event.type == "final":
                tester_answer = event.message
            elif event.type == "error":
                saw_error = True

        report: TesterOutput | None = None
        # 情况 1：tester 中途报错（例如达到 max_iterations），
        #         此时根本没有合法 final 产出，直接返回 None，
        #         由调用方根据 events 里的 error 事件判断真实原因。
        if saw_error and not tester_answer:
            return events, None

        # 情况 2：tester 有 final 产出，尝试解析成 TesterOutput。
        try:
            report = parse_structured_output(
                tester_answer,
                TesterOutput,
            )
        except Exception:
            report = None
        return events, report

    def run_stream(self, question: str) -> Iterator[AgentEvent]:
        """多智能体流水线的流式入口（含 Planner），按阶段 yield 各类 AgentEvent。

        整体流程：
        1. Planner 出计划 → 解析成 PlanStep 列表；
        2. 交给 `_execute_plan` 执行「Coder → Tester → Reviewer」的后半段。

        如果想跳过 Planner（例如计划已由用户批准、从 `/api/tasks/plan` 拿到），
        请改用 `execute_stream(question, plan)`。

        Args:
            question: 用户下发的开发任务描述。

        Yields:
            AgentEvent: 每个阶段的 start / thinking / tool_* / hand_off / final / error 事件，
                前端据此实时渲染整个多智能体协作过程。
        """
        state = AgentState(repo_path=self.repo_path, question=question, status="running", answer="")
        yield AgentEvent(type="start", agent="orchestrator", message="DevPilot 多智能体任务启动")

        # ----- 阶段 1：Planner 制定计划 -----
        planner_answer = ""
        for event in self.planner.run_stream(question=question):
            yield event
            if event.type == "final":
                planner_answer = event.message

        # 解析 planner 输出的结构化计划。模型可能带开场白或 Markdown 围栏，
        # 统一交给 structured_output 服务提取 JSON 并校验模型。
        try:
            planner_output = parse_structured_output(
                planner_answer,
                PlannerOutput,
            )
            state.plan = planner_output.steps
        except Exception as exc:
            state.status = "failed"
            yield AgentEvent(type="error", agent="orchestrator", message=f"Plan 解析失败：{exc}")
            return
        # hand_off 事件：告知前端「计划已定，接下来是 Coder 的戏份」。
        # data 里带上完整计划，便于前端单独展示计划面板。
        yield AgentEvent( type="hand_off",
            agent="orchestrator",
            message=(
                "Planner 已完成，"
                "任务移交给 Coder"
            ),
            data={
                "plan": [
                    step.model_dump()
                    for step in state.plan
                ]
            },
        )

        # 计划已就绪，交给统一的「执行段」跑 Coder → Tester → Reviewer。
        # yield from 会逐条透传子生成器事件，同时保持流式响应；若改成普通
        # 函数调用，只会得到生成器对象，Coder/Tester/Reviewer 实际不会运行。
        yield from self._execute_plan(state=state, question=question)

    def execute_stream(self, question: str, plan: list[PlanStep]) -> Iterator[AgentEvent]:
        """多智能体流水线的流式入口（跳过 Planner，直接用传入的计划）。

        用于「先出计划 → 用户批准 → 再执行」的两段式流程：
        前端先调 `plan()`（或 `/api/tasks/plan`）拿到计划、用户确认后，
        把已批准的计划传入本方法，直接从 Coder 阶段开始执行，避免重复跑 Planner。

        Args:
            question: 用户下发的开发任务描述。
            plan: 已批准、待执行的开发计划步骤列表（来自 Planner/`plan()` 的产出）。

        Yields:
            AgentEvent: 从 start 事件开始，随后是 Coder → Tester → Reviewer 的各类事件。
        """
        state = AgentState(repo_path=self.repo_path, question=question, status="running", answer="")
        # 直接用传入的计划，跳过 Planner 阶段。
        state.plan = plan
        yield AgentEvent(type="start", agent="orchestrator", message="DevPilot 多智能体任务启动（使用已批准的计划）")

        # hand_off 事件：前端此时已知道计划，直接进入 Coder。
        yield AgentEvent(
            type="hand_off",
            agent="orchestrator",
            message="使用已批准的计划，任务移交给 Coder",
            data={"plan": [step.model_dump() for step in plan]},
        )

        # 交给统一的「执行段」跑 Coder → Tester → Reviewer。
        yield from self._execute_plan(state=state, question=question)

    def _execute_plan(self, state: AgentState, question: str) -> Iterator[AgentEvent]:
        """执行「Coder → Tester → Reviewer」的后半段流水线（Planner 之后的公共逻辑）。

        被 `run_stream`（内部跑 Planner）和 `execute_stream`（外部传入计划）共用，
        避免两处重复代码。前置条件：`state.plan` 已就绪。

        Args:
            state: 已含 repo_path / question / plan 的 AgentState 快照，本方法会原地更新
                其 test_report / review_report / repair_round / status / answer 字段。
            question: 用户下发的开发任务描述。

        Yields:
            AgentEvent: Coder / Tester / Reviewer 各阶段的事件。
        """
        # ----- 阶段 2：Coder 按计划改代码 -----
        # 把「原始任务 + 计划」打包成一段提示词交给 coder，
        # 让 coder 既知道大目标，又知道每一步该做什么。
        # model_dump() 先把 PlanStep 转为普通字典，再序列化成格式化 JSON。
        # 相比直接 str(state.plan)，字段边界更清楚，也不会把 Pydantic repr
        # 细节泄漏到给 Coder 的提示词中。
        coder_question=f"""
            用户原始任务：

            {question}

            Planner 制定的开发计划：

            {json.dumps(
                [
                    step.model_dump()
                    for step in state.plan
                ],
                ensure_ascii=False,
                indent=2
            )}

            请按照计划修改代码。
            """
        for event in self.coder.run_stream(coder_question):
            yield event

        # coder 改完，hand_off 交给 tester。
        yield AgentEvent(type="hand_off",
            agent="orchestrator",
            message=(
                "coder 已完成，"
                "任务移交给 tester验证"
            ),
        )

        #---------------
        # 阶段 3：Tester 验证 + 失败自动返工
        # 关键点：测试失败后必须「coder 修复 → 重新跑 tester」，
        # 让 while 条件基于**最新**的测试结果，而不是第一次那个 False。
        #---------------
        tester_events, state.test_report = self._run_tester(
            "请验证当前代码修改。"
            "运行测试，并根据真实测试结果"
            "判断本次修改是否通过。"
        )
        for event in tester_events:
            yield event
        if state.test_report is None:
            # 这里 None 有两种可能：tester 中途报错（如达到 max_iterations）
            # 或 final 内容不是合法 TesterOutput JSON。统一给一个准确提示，
            # 具体原因可回看上面 tester 产出的 error 事件。
            yield AgentEvent(
                type="error",
                agent="orchestrator",
                message="Tester 未产出有效的测试报告（可能达到最大迭代次数或 JSON 解析失败）",
            )
            return

        # 返工循环：测试没通过 且 还有返工额度 时才进。
        # 每轮 = coder 修复 + 重新测试，刷新 state.test_report 后再判断。
        while (
            state.test_report
            and not state.test_report.passed
            and state.repair_round < self.max_repair_rounds
        ):
            state.repair_round += 1
            yield AgentEvent(
                type="hand_off",
                agent="orchestrator",
                message=f"当前测试失败，进入第 {state.repair_round} 次返工",
            )

            repair_question = f"""
                当前修改测试失败。

                测试反馈：

                {state.test_report.model_dump_json(indent=2)}

                请根据测试错误继续修复代码。
                修复后检查 git diff。
            """
            # 1. 让 coder 根据测试反馈修复。反馈用 model_dump_json 序列化，
            #    把 stdout/stderr 一起塞给 coder，让它知道到底哪里挂了。
            for event in self.coder.run_stream(repair_question):
                yield event

            # 2. 关键：修复后重新跑 tester，刷新 state.test_report，
            #    否则 while 条件永远停留在第一次的 False，循环会空转。
            tester_events, state.test_report = self._run_tester(
                "请重新运行测试，"
                "判断刚才的修复是否让测试通过。"
            )
            for event in tester_events:
                yield event
            if state.test_report is None:
                yield AgentEvent(
                    type="error",
                    agent="orchestrator",
                    message="返工后 Tester 仍未产出有效测试报告（可能达到最大迭代次数或 JSON 解析失败）",
                )
                return

        # 返工额度耗尽后仍失败，必须在这里终止。否则下面会错误地显示
        # “测试通过”，并可能让失败样本被评测系统计为成功。
        if not state.test_report.passed:
            state.status = "failed"
            yield AgentEvent(
                type="error",
                agent="orchestrator",
                message="已达到最大返工次数，但测试仍未通过",
                data={
                    "test_report": state.test_report.model_dump(),
                    "repair_rounds": state.repair_round,
                },
            )
            return

        # ----- 阶段 4：Reviewer 独立审查 -----
        yield AgentEvent(
            type="hand_off",
            agent="orchestrator",
            message="测试通过，进入 Reviewer 代码审查",
        )
        # 这里只保存 final 文本；thinking/tool_result 事件已实时转发给前端，
        # 但不应该混进 ReviewerOutput 的结构化 JSON 解析输入。
        reviewer_answer=" "
        for event in self.reviewer.run_stream(question=f"""
            用户原始需求：

            {question}

            请查看当前 git diff，
            独立审查 Coder 的修改是否满足需求。
            """):
            yield event
            if event.type=="final":
                reviewer_answer=event.message

        # 解析 reviewer 的审查结论。注意这里和 planner/tester 一样，
        # 模型输出可能带废话，统一交给 structured_output 服务处理。
        try:
            state.review_report = parse_structured_output(
                reviewer_answer,
                ReviewerOutput,
            )
        except Exception as exc:
            state.status = "failed"
            yield AgentEvent(
                type="error",
                agent="orchestrator",
                message=f"Reviewer 输出解析失败：{exc}",
                data={"repair_rounds": state.repair_round},
            )
            return

        # reviewer 放行 → 任务完成，产出最终 final 事件。
        # data 汇总计划 / 测试报告 / 审查报告 / 返工轮数，前端可据此出完整报告。
        if state.review_report.approved:
            state.status="completed"
            state.answer="DevPilot 已完成开发任务，测试与代码审查均通过。"
            yield AgentEvent(
                type="final",
                agent="orchestrator",
                message=state.answer,
                data={
                    "plan":[x.model_dump()for x in state.plan],
                    "test_report":(
                        state.test_report.model_dump() if state.test_report else None
                    ),
                    "review_report":(
                        state.review_report.model_dump() if state.review_report else None
                    ),
                    "repair_rounds":(state.repair_round),
                }
            )
            return

        # Reviewer 拒绝也属于完整、可观测的失败结果。显式 error 事件既能
        # 告知 API 调用方，也能让 Evals runner 正确提取失败原因。
        state.status = "failed"
        yield AgentEvent(
            type="error",
            agent="orchestrator",
            message="Reviewer 未批准当前修改",
            data={
                "review_report": state.review_report.model_dump(),
                "repair_rounds": state.repair_round,
            },
        )

    def plan (self,question:str,)->list[PlanStep]:
        """仅跑一次 Planner、解析出计划就返回，不进入完整流水线。

        用于前端「先看计划再决定是否批准执行」的场景。

        Args:
            question: 用户下发的开发任务描述。

        Returns:
            list[PlanStep]: 解析后的开发计划步骤列表（来自 Planner JSON 的 steps 字段）。

        Raises:
            ValueError: Planner 输出无法解析为合法 JSON、或 steps 缺失/为空时抛出。
                避免静默返回空计划，让调用方（/api/tasks/plan）能返回明确错误。
        """
        planner_answer=""
        for event in self.planner.run_stream(question=question):
            if event.type=="final":
                planner_answer=event.message
        # 防御：Planner 可能因达到 max_iterations 而没产出 final，
        # 或输出了不含 steps / steps 为空的 JSON。PlannerOutput 会统一校验。
        planner_output = parse_structured_output(
            planner_answer,
            PlannerOutput,
        )

        return planner_output.steps
