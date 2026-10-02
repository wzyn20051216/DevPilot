"""! @brief 与 HTTP 连接解耦的后台任务执行服务。"""

import json
import threading
from collections.abc import Callable, Iterator
from typing import Protocol

from loguru import logger

from ..agents.single_developer_agent import SingleDeveloperAgent
from ..database.task_repository import TaskRepository
from ..models.agent_state import AgentEvent, PlanStep
from ..models.task import DevelopmentTask


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
