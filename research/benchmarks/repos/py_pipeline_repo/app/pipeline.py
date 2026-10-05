"""! @brief 从队列拉取任务并处理，瞬时失败时重试。"""

from app.storage.queue import QueueEmpty, TaskQueue


class Pipeline:
    """! @brief 简单任务管线：失败任务回推队列重试，超过上限则放弃。"""

    def __init__(self, queue: TaskQueue, max_retries: int = 1) -> None:
        self.queue = queue
        self.max_retries = max_retries
        self._attempts: dict[str, int] = {}

    def process(self, task: str) -> str:
        """! @brief 处理单个任务。

        ``flaky`` 前缀任务第一次失败、之后成功（瞬时故障）；
        ``fail`` 前缀任务永久失败。
        """

        self._attempts[task] = self._attempts.get(task, 0) + 1
        if task.startswith("flaky") and self._attempts[task] == 1:
            raise RuntimeError("transient failure")
        if task.startswith("fail"):
            raise RuntimeError("permanent failure")
        return f"done:{task}"

    def run(self, tasks: list[str]) -> list[str]:
        """! @brief 处理全部任务，返回成功处理的任务结果列表。"""

        for task in tasks:
            self.queue.push(task)

        results: list[str] = []
        while True:
            try:
                task = self.queue.pop()
            except QueueEmpty:
                break
            if task is None:
                # 防御性哨兵：queue 当前在空队列时返回 None 而非抛异常，
                # 这里把 None 视为结束条件，避免把 None 当作任务处理。
                break
            try:
                results.append(self.process(task))
            except RuntimeError:
                # BUG（调用假设）：重试回推条件方向写反，第一次失败本应回推
                # 却被直接丢弃，导致“重试期间任务丢失”。
                if self._attempts[task] > self.max_retries:
                    self.queue.push(task)
        return results
