"""! @brief 独立 Sandbox Worker 进程入口（技术手册 10.2.5）。

Worker 通过持久化任务队列领取任务并在本地进程执行，把 API 从耗时的
Agent 执行与 Sandbox 调度中解耦。启动方式：``python -m backend.src.worker``。

设计要点：
- **租约 + 心跳**：领取时写入 ``lease_expires_at``，后台线程周期性续租；
  SQLite 租约到期后隔离任务，需确认旧进程停止再显式恢复；
- **幂等**：入队靠 ``task_id`` 唯一约束去重，执行靠 ``claim_status`` 的
  原子状态迁移 + ``attempts`` 上限把反复失败任务送进死信；
- **优雅退出**：收到 SIGINT 后不再领取新任务，等待在跑任务结束（最多 2
  个租约周期），超时后进程退出，未完成任务由租约到期机制标记中断。
"""

import argparse
import os
import signal
import socket
import threading
import time
from collections.abc import Callable

from loguru import logger

from .agents.orchestrator import DevPilotOrchestrator
from .config import settings
from .database.connection import init_database
from .database.task_repository import task_repository
from .logging_config import configure_logging
from .security import require_repo_path
from .services.task_execution_service import (
    SingleAgentTaskRunner,
    TaskExecutionService,
)
from .services.task_queue import get_task_queue
from .services.task_policy import decide_task_strategy, task_rag_enabled


def _worker_runner_factory(task, cancel_check: Callable[[], bool]):
    """! @brief 按任务执行策略构造运行器，镜像 ``main._create_task_runner``。

    不在 Worker 里 import ``main``：``main`` 模块导入时即实例化 FastAPI
    应用并注册大量路由，而 Worker 只需要执行能力，引入它会无谓拖慢启动
    并引入 HTTP 相关副作用。通过共享 task_rag_enabled 保持两种入口策略一致。
    """
    require_repo_path(task.repo_path)
    enable_rag = task_rag_enabled(task)
    if task.execution_mode.startswith("single_"):
        return SingleAgentTaskRunner(
            repo_path=task.repo_path,
            enable_rag=enable_rag,
            cancel_check=cancel_check,
            strategy=decide_task_strategy(task),
        )
    return DevPilotOrchestrator(
        repo_path=task.repo_path,
        enable_rag=enable_rag,
        cancel_check=cancel_check,
    )


def make_fence_check(queue, task_id: str, worker_id: str, fence_token: int):
    """! @brief 构造带短缓存的租约校验回调，供执行服务在写盘前调用。

    执行服务每个事件都会问一次"我还持有这个任务吗"，直接查库会让写事件
    的开销翻倍。缓存 1 秒的判定结果：失去租约的执行者最多多写 1 秒的
    事件，但正常情况下数据库压力可忽略。

    @return 无参谓词，True 表示仍持有租约。
    """

    state = {"checked_at": 0.0, "held": True}

    def check() -> bool:
        now = time.monotonic()
        if now - state["checked_at"] < 1.0:
            return bool(state["held"])
        state["held"] = queue.is_current(task_id, worker_id, fence_token)
        state["checked_at"] = now
        return bool(state["held"])

    return check


def run_worker_once(
    queue,
    service: TaskExecutionService,
    worker_id: str,
) -> bool:
    """! @brief 领取并执行至多一个任务，供测试与 ``--once`` 直接复用。

    @return 是否领取到任务（False 表示队列暂无候选）。
    """
    queue.reclaim_expired()
    entry = queue.claim(worker_id)
    if entry is None:
        return False

    task_id = entry.task_id
    # 队列侧已领取，还要在 tasks 表里做一次原子状态迁移，防止同一任务被
    # API 与 Worker、或多个 Worker 重复执行。迁移失败说明任务状态已变
    # （例如已 running/cancelled），此时归还队列，交给 attempts 上限兜底。
    if not service.repository.claim_status(
        task_id,
        {"awaiting_approval", "interrupted"} | ({"failed"} if entry.attempts > 1 else set()),
        "running",
    ):
        queue.complete(
            task_id,
            succeeded=False,
            error="任务状态不允许执行",
            worker_id=worker_id,
            fence_token=entry.fence_token,
        )
        return True

    service.start(task_id, make_fence_check(queue, task_id, worker_id, entry.fence_token))
    poll = settings.task_worker_poll_seconds / 4
    next_heartbeat = 0.0
    while service.is_running(task_id):
        if service.repository.get_task(task_id).status == "cancelling":
            service.cancel(task_id)
        if time.monotonic() >= next_heartbeat:
            if not queue.heartbeat(worker_id, task_id, entry.fence_token):
                service.cancel(task_id)
            next_heartbeat = time.monotonic() + settings.task_worker_heartbeat_seconds
        time.sleep(poll)
    status = service.repository.get_task(task_id).status
    queue.complete(
        task_id,
        succeeded=status == "completed",
        worker_id=worker_id,
        fence_token=entry.fence_token,
    )
    return True


def _settle_running(
    queue,
    service: TaskExecutionService,
    inflight: dict[str, int],
    lock: threading.Lock,
    worker_id: str,
) -> None:
    """! @brief 结算本 Worker 已跑完的任务，及时在队列侧收口。

    主循环每次迭代先结算上一轮结束的任务，避免任务在 claim 与 complete
    之间长时间停留在 claimed 状态；租约虽能兜底，但及时收口让队列统计更准。

    ``inflight`` 的值是该任务本次领取的 fencing 令牌，结算时一并带回，
    保证被回收过（令牌已失效）的任务不会被旧 Worker 覆盖成 done。
    """
    with lock:
        entries = list(inflight.items())
    for task_id, fence_token in entries:
        if service.is_running(task_id):
            # 协作式取消轮询：API 进程只把任务标记为 cancelling（跨进程
            # 无法直接调用本进程的 service.cancel），Worker 在这里发现
            # cancelling 状态后触发本地协作式取消。
            try:
                if service.repository.get_task(task_id).status == "cancelling":
                    service.cancel(task_id)
            except Exception:  # noqa: BLE001
                logger.exception("任务 {} 取消轮询失败", task_id)
            continue
        try:
            status = service.repository.get_task(task_id).status
            queue.complete(
                task_id,
                succeeded=status == "completed",
                worker_id=worker_id,
                fence_token=fence_token,
            )
        except Exception:  # noqa: BLE001
            logger.exception("结算任务 {} 状态失败", task_id)
        finally:
            with lock:
                inflight.pop(task_id, None)


def main() -> None:
    """! @brief Worker 主入口：初始化、心跳线程、领取-执行主循环、优雅退出。"""

    parser = argparse.ArgumentParser(description="DevPilot Sandbox Worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="处理至多一个任务后退出",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="覆盖队列轮询间隔（调试用）",
    )
    args = parser.parse_args()

    configure_logging()
    init_database()

    queue = get_task_queue()
    service = TaskExecutionService(
        repository=task_repository,
        orchestrator_factory=_worker_runner_factory,
    )
    # config.py 未预置 task_worker_id 字段，用 getattr 兜底以免改动 config；
    # 默认 hostname:pid，多 Worker 部署时能区分租约归属。
    worker_id = getattr(settings, "task_worker_id", "") or (
        f"{socket.gethostname()}:{os.getpid()}"
    )

    if args.once:
        run_worker_once(queue, service, worker_id)
        return

    interval = (
        args.interval if args.interval is not None else settings.task_worker_poll_seconds
    )

    stop_event = threading.Event()
    heartbeat_stop = threading.Event()
    lock = threading.Lock()
    # 在飞任务 → 本次领取的 fencing 令牌。
    inflight: dict[str, int] = {}

    def heartbeat_loop() -> None:
        """! @brief 周期性对在飞任务续租，防止长任务被误判为崩溃。"""
        while not heartbeat_stop.wait(settings.task_worker_heartbeat_seconds):
            with lock:
                entries = list(inflight.items())
            for task_id, fence_token in entries:
                try:
                    if not queue.heartbeat(worker_id, task_id, fence_token):
                        service.cancel(task_id)
                except Exception:  # noqa: BLE001
                    logger.exception("任务 {} 心跳失败", task_id)

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        name="devpilot-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()

    def _handle_signal(_signum, _frame) -> None:
        logger.info("收到退出信号，停止领取新任务")
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info(
        "Worker {} 启动（queue backend={}）",
        worker_id,
        settings.task_queue_backend,
    )

    while not stop_event.is_set():
        try:
            queue.reclaim_expired()
            _settle_running(queue, service, inflight, lock, worker_id)
            # 消费侧背压：在飞任务达到上限时不再领取新任务，让积压留在队列里
            # （入队侧的深度上限负责拒绝更多新任务），而不是把本进程压垮。
            with lock:
                at_capacity = len(inflight) >= settings.task_worker_max_concurrency
            if at_capacity:
                continue
            entry = queue.claim(worker_id)
            if entry is None:
                continue
            task_id = entry.task_id
            if not service.repository.claim_status(
                task_id,
                {"awaiting_approval", "interrupted"} | ({"failed"} if entry.attempts > 1 else set()),
                "running",
            ):
                queue.complete(
                    task_id,
                    succeeded=False,
                    error="任务状态不允许执行",
                    worker_id=worker_id,
                    fence_token=entry.fence_token,
                )
                continue
            with lock:
                inflight[task_id] = entry.fence_token
            service.start(
                task_id,
                make_fence_check(queue, task_id, worker_id, entry.fence_token),
            )
        except Exception:  # noqa: BLE001
            # 单次 claim/execute 失败不能杀死主循环：记录后继续下一轮。
            logger.exception("Worker 主循环异常，继续下一轮")
        finally:
            # 无论是否领到任务都按间隔休眠，避免空转打满 CPU。
            time.sleep(interval)

    # 优雅退出：不再领新任务，等待在跑任务结束。最长等待 2 个租约周期，
    # 超时后进程直接退出，未完成任务会在租约到期后隔离，等待显式恢复。
    deadline = time.monotonic() + 2 * settings.task_worker_lease_seconds
    while time.monotonic() < deadline:
        _settle_running(queue, service, inflight, lock, worker_id)
        with lock:
            still_running = any(service.is_running(tid) for tid in inflight)
        if not still_running:
            break
        time.sleep(min(1.0, interval))
    heartbeat_stop.set()
    heartbeat_thread.join(timeout=1.0)
    logger.info("Worker {} 退出", worker_id)


if __name__ == "__main__":
    main()
