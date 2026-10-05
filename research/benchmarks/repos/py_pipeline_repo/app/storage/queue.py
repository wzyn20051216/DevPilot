"""! @brief 任务队列，供 Pipeline 拉取待处理任务。"""


class QueueEmpty(Exception):
    """! @brief 队列为空时抛出。"""


class TaskQueue:
    """! @brief 简单的 FIFO 任务队列。"""

    def __init__(self) -> None:
        self._pending: list[str] = []

    def push(self, task: str) -> None:
        self._pending.append(task)

    def pop(self) -> str:
        # BUG（异常语义变化）：空队列应 raise QueueEmpty，这里却返回 None，
        # 调用方会把 None 误判为“队列已空”，破坏上层对异常的假设。
        if not self._pending:
            return None  # type: ignore[return-value]
        return self._pending.pop(0)

    def __len__(self) -> int:
        return len(self._pending)
