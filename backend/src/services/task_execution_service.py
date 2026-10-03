"""! @brief 与 HTTP 连接解耦的后台任务执行服务。"""

import json
import threading
from collections.abc import Callable, Iterator
from typing import Any, Protocol

from loguru import logger

from ..agents.single_developer_agent import SingleDeveloperAgent
from ..config import settings
from ..database.task_repository import TaskRepository
from ..models.agent_state import AgentEvent, PlanStep
from ..models.task import DevelopmentTask
from . import context_store


class OrchestratorProtocol(Protocol):
    """执行服务依赖的最小编排器协议。"""

    def execute_stream(self, question: str, plan: list) -> Iterator[AgentEvent]: ...


class SingleAgentTaskRunner:
    """! @brief 让单 Agent 复用已审批计划的任务执行适配器。"""

    def __init__(
        self,
        repo_path: str,
        enable_rag: bool,
        cancel_check: Callable[[], bool],
    ) -> None:
        self.agent = SingleDeveloperAgent(
            repo_path=repo_path,
            enable_rag=enable_rag,
            cancel_check=cancel_check,
        )

    def execute_stream(
        self,
        question: str,
        plan: list[PlanStep],
    ) -> Iterator[AgentEvent]:
        """! @brief 将用户需求与批准计划一起交给单 Agent。"""

        prompt = (
            f"用户原始任务：\n{question}\n\n"
            "用户已批准的执行计划：\n"
            + json.dumps(
                [step.model_dump() for step in plan],
                ensure_ascii=False,
                indent=2,
            )
        )
        yield from self.agent.run_stream(prompt)


OrchestratorFactory = Callable[[DevelopmentTask, Callable[[], bool]], OrchestratorProtocol]


class TaskExecutionService:
    """! @brief 在后台线程执行任务，并把事件持续写入 SQLite。

    HTTP/SSE 仅负责观察持久化事件。浏览器断开不会关闭 Agent 生成器，任务可在
    后台继续完成；取消信号会在 LLM 或工具调用之间的安全点被消费。
    """

    def __init__(
        self,
        repository: TaskRepository,
        orchestrator_factory: OrchestratorFactory,
    ) -> None:
        self.repository = repository
        self.orchestrator_factory = orchestrator_factory
        self._lock = threading.Lock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}

    def start(self, task_id: str) -> None:
        """! @brief 启动已原子迁移到 running 的任务。"""

        with self._lock:
            current = self._threads.get(task_id)
            if current is not None and current.is_alive():
                raise RuntimeError("任务已经在当前进程中执行")
            cancel_event = threading.Event()
            thread = threading.Thread(
                target=self._run,
                args=(task_id, cancel_event),
                name=f"devpilot-task-{task_id[:8]}",
                daemon=True,
            )
            self._cancel_events[task_id] = cancel_event
            self._threads[task_id] = thread
            thread.start()

    def cancel(self, task_id: str) -> bool:
        """! @brief 请求取消正在本进程执行的任务。"""

        with self._lock:
            cancel_event = self._cancel_events.get(task_id)
            thread = self._threads.get(task_id)
            if cancel_event is None or thread is None or not thread.is_alive():
                return False
            cancel_event.set()
            return True

    def is_running(self, task_id: str) -> bool:
        """! @brief 判断任务是否仍由当前进程的后台线程持有。"""

        with self._lock:
            thread = self._threads.get(task_id)
            return thread is not None and thread.is_alive()

    def _persist_event(self, task_id: str, sequence: int, event: AgentEvent) -> None:
        """! @brief 持久化事件及其中的工具调用观测。"""

        self.repository.add_event(task_id=task_id, sequence=sequence, event=event)
        if event.type != "tool_result":
            return
        tool = event.data.get("tool")
        arguments = event.data.get("arguments", {})
        if isinstance(tool, str) and isinstance(arguments, dict):
            self.repository.add_tool_call(
                task_id=task_id,
                agent=event.agent,
                iteration=event.iteration,
                tool=tool,
                arguments=arguments,
                result_preview=str(event.data.get("result_preview", "")),
                duration_seconds=float(event.data.get("elapsed_seconds", 0.0) or 0.0),
                succeeded=bool(event.data.get("succeeded", True)),
            )

    def _make_checkpoint_callback(
        self,
        task_id: str,
    ) -> Callable[[str, list[dict[str, Any]]], None]:
        """! @brief 构造检查点写入回调；写失败绝不影响任务执行。

        检查点持久化是尽力而为的旁路逻辑：Agent 每轮迭代都会回调一次，若
        数据库写入失败绝不能打断 tool-calling 主循环或任务收尾，因此这里把
        所有异常降级为 debug 日志。
        """

        def callback(agent_name: str, messages: list[dict[str, Any]]) -> None:
            try:
                context_store.save_context(
                    task_id,
                    agent_name,
                    messages,
                    max_messages=settings.agent_checkpoint_max_messages,
                )
            except Exception:  # noqa: BLE001
                logger.debug("任务 {} 检查点写入失败，忽略", task_id)

        return callback

    @staticmethod
    def _restorable_agents(orchestrator: OrchestratorProtocol) -> list[Any]:
        """! @brief 从编排器上收集所有可做上下文恢复的角色 agent。

        单 Agent 模式（SingleAgentTaskRunner）暴露单个 .agent 属性；多 Agent
        模式（DevPilotOrchestrator）暴露 planner/coder/tester/reviewer 四个角色。
        统一成「暴露了 checkpoint_callback 的 BaseToolAgent 实例」列表返回，
        让恢复注入逻辑对两种模式一视同仁。

        用 getattr 鸭子类型探测而非 isinstance，避免在 service 层硬依赖具体
        agent 类，保持与 OrchestratorFactory 协议的解耦。
        """
        # 单 Agent：SingleAgentTaskRunner.agent
        single = getattr(orchestrator, "agent", None)
        if single is not None:
            return [single]

        # 多 Agent：DevPilotOrchestrator 的四个角色。
        agents: list[Any] = []
        for role in ("planner", "coder", "tester", "reviewer"):
            agent = getattr(orchestrator, role, None)
            if agent is not None and hasattr(agent, "checkpoint_callback"):
                agents.append(agent)
        return agents

    def _run(self, task_id: str, cancel_event: threading.Event) -> None:
        """! @brief 完整消费编排器事件，任务结果不依赖任何客户端连接。"""

        sequence = self.repository.next_event_sequence(task_id)
        saw_error = False
        last_event_type: str | None = None
        try:
            task = self.repository.get_task(task_id)
            orchestrator = self.orchestrator_factory(
                task,
                cancel_event.is_set,
            )
            # ---- 上下文断点恢复（技术手册 10.1）----
            # 单 Agent 与多 Agent 统一处理：从编排器上收集所有暴露了
            # checkpoint_callback 的角色 agent，逐个按 agent.name 恢复各自的
            # 中断前上下文，并挂上写回调。SingleAgentTaskRunner 暴露 .agent，
            # DevPilotOrchestrator 暴露 planner/coder/tester/reviewer 四个。
            if settings.agent_checkpoint_enabled:
                for agent in self._restorable_agents(orchestrator):
                    restored = None
                    try:
                        restored = context_store.load_context(task_id, agent.name)
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "任务 {} 的 {} 检查点读取失败，按全新上下文执行",
                            task_id,
                            agent.name,
                        )
                    if restored:
                        agent.initial_messages = restored
                        # 产出一条可观测事件，让前端/追踪知道本次是恢复执行。
                        # _run 不是生成器，这里在进入事件循环前手动持久化一次，
                        # 与下方 error/cancelled 事件的 _persist_event 用法一致。
                        restore_event = AgentEvent(
                            type="thinking",
                            agent="orchestrator",
                            message=f"已从上下文检查点恢复 {len(restored)} 条消息",
                            data={
                                "restored_messages": len(restored),
                                "agent": agent.name,
                            },
                        )
                        self._persist_event(task_id, sequence, restore_event)
                        sequence += 1
                    # 无论是否命中检查点都要挂上写回调，让本次运行的中间状态
                    # 持续落盘，供下一次重启后继续恢复。
                    agent.checkpoint_callback = self._make_checkpoint_callback(task_id)

            for event in orchestrator.execute_stream(
                question=task.question,
                plan=task.plan,
            ):
                self._persist_event(task_id, sequence, event)
                sequence += 1
                last_event_type = event.type
                if event.type == "error":
                    saw_error = True
                if event.type == "cancelled" or cancel_event.is_set():
                    break

            if cancel_event.is_set():
                cancelled_event = AgentEvent(
                    type="cancelled",
                    agent="orchestrator",
                    message="任务已按用户请求取消",
                )
                # 子 Agent 已发出 cancelled 时不再写重复事件。
                if last_event_type != "cancelled":
                    self._persist_event(task_id, sequence, cancelled_event)
                self.repository.claim_status(
                    task_id,
                    {"running", "cancelling"},
                    "cancelled",
                )
            else:
                self.repository.claim_status(
                    task_id,
                    {"running"},
                    "failed" if saw_error else "completed",
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Task {} execution failed", task_id)
            error_event = AgentEvent(
                type="error",
                agent="orchestrator",
                message=str(exc),
            )
            try:
                self._persist_event(task_id, sequence, error_event)
                self.repository.claim_status(
                    task_id,
                    {"running", "cancelling"},
                    "failed",
                )
            except Exception:  # noqa: BLE001
                logger.exception("Task {} failure state could not be persisted", task_id)
        finally:
            with self._lock:
                self._cancel_events.pop(task_id, None)
                self._threads.pop(task_id, None)


def encode_sse_event(event: dict[str, object]) -> str:
    """! @brief 将持久化事件编码为标准 SSE data block。"""

    payload = {
        "type": event["type"],
        "agent": event["agent"],
        "iteration": event["iteration"],
        "message": event["message"],
        "data": event["data"],
        "sequence": event["sequence"],
        "created_at": event["created_at"],
    }
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
