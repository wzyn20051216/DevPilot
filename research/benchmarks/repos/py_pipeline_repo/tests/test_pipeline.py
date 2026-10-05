"""! @brief Pipeline 跨文件缺陷的独立验收测试。"""

import pytest

from app.pipeline import Pipeline
from app.storage.queue import QueueEmpty, TaskQueue


def test_pop_raises_on_empty_queue() -> None:
    """空队列 pop 应抛出 QueueEmpty，而不是返回 None。"""

    queue = TaskQueue()
    with pytest.raises(QueueEmpty):
        queue.pop()


def test_queue_is_fifo() -> None:
    """队列应按 FIFO 顺序出队。"""

    queue = TaskQueue()
    queue.push("a")
    queue.push("b")
    assert queue.pop() == "a"
    assert queue.pop() == "b"


def test_simple_task_completes() -> None:
    """普通任务应直接成功。"""

    queue = TaskQueue()
    pipeline = Pipeline(queue, max_retries=1)
    assert pipeline.run(["ok"]) == ["done:ok"]


def test_flaky_task_survives_retry() -> None:
    """瞬时失败的任务应在重试后成功，而不是在重试期间丢失。"""

    queue = TaskQueue()
    pipeline = Pipeline(queue, max_retries=1)
    assert pipeline.run(["ok", "flaky"]) == ["done:ok", "done:flaky"]


def test_permanent_failure_terminates() -> None:
    """永久失败的任务在重试上限后应被丢弃，且不得无限重试。"""

    queue = TaskQueue()
    pipeline = Pipeline(queue, max_retries=1)
    assert pipeline.run(["fail:boom"]) == []
