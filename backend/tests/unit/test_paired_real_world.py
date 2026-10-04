"""! @brief 验证配对实验计划在续跑和重复实验时保持完整。"""

from backend.scripts.paired_real_world import _is_provider_blocked, _schedule


def test_every_instance_repeat_contains_one_pair() -> None:
    """! @brief 三题三次重复必须得到 18 个互不重复的作业。"""

    jobs = _schedule(["a", "b", "c"], 3)
    assert len(jobs) == 18
    assert len({(j["instance_id"], j["repeat_index"], j["variant"]) for j in jobs}) == 18
    for index in range(0, len(jobs), 2):
        left, right = jobs[index:index + 2]
        assert (left["instance_id"], left["repeat_index"]) == (
            right["instance_id"], right["repeat_index"],
        )
        assert {left["variant"], right["variant"]} == {"single_no_rag", "single_adaptive"}


def test_pair_order_alternates_and_is_reproducible() -> None:
    """! @brief AB/BA 次序确定，避免全部基线先跑造成时间段偏差。"""

    jobs = _schedule(["a", "b", "c"], 3)
    first_variants = [jobs[index]["variant"] for index in range(0, len(jobs), 2)]
    assert first_variants == ["single_no_rag", "single_adaptive"] * 4 + ["single_no_rag"]
    assert jobs == _schedule(["a", "b", "c"], 3)


def test_provider_errors_are_distinct_from_algorithm_failures() -> None:
    """! @brief 额度与限流中断不能计入修复失败，也不能被续跑当作完成。"""

    assert _is_provider_blocked({"error": "Error code: 402 - Insufficient Balance"})
    assert _is_provider_blocked({"error": "Error code: 429 - rate limit"})
    assert not _is_provider_blocked({"error": "test_402.py::test_429 failed"})
    assert not _is_provider_blocked({"error": None})
