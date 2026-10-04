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


def test_each_case_changes_order_even_when_number_of_cases_is_even() -> None:
    """! @brief 两题计划也必须逐轮交换先后，避免任务难度与时间段绑定。"""

    jobs = _schedule(["a", "b"], 3)
    for instance in ["a", "b"]:
        first = [
            jobs[index]["variant"] for index in range(0, len(jobs), 2)
            if jobs[index]["instance_id"] == instance
        ]
        assert first[0] != first[1]
        assert first[0] == first[2]


def test_resume_retries_provider_interruption_and_preserves_original_trace(monkeypatch, tmp_path) -> None:
    """! @brief 模拟 402 后续跑，验证原作业重试且中断轨迹不被覆盖。"""

    import json
    import sys
    from types import SimpleNamespace

    import pytest
    from backend.scripts import paired_real_world as driver
    from backend.src import config, evals

    env_file = tmp_path / ".env"
    env_file.write_text("LLM_API_KEY=test-key\nLLM_MODEL=test-model\n", encoding="utf-8")
    (tmp_path / "swebench_datasets").mkdir()
    (tmp_path / "swebench_datasets/verified.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(config, "settings", config.Settings(_env_file=env_file))
    monkeypatch.setattr(driver, "_code_hash", lambda root: "frozen-source")
    monkeypatch.setattr(driver.subprocess, "check_output", lambda *args, **kwargs: "image-id\n")
    monkeypatch.setattr(sys, "argv", [
        "paired", "--data-root", str(tmp_path), "--env-file", str(env_file),
        "--run-id", "resume", "--instance", "a", "--repeats", "1",
    ])
    calls = []
    run_root = tmp_path / "paired_real_world/resume"

    def evaluate(instance, variant, run_id, excluded_targets, repeat_index):
        """! @brief 第一次模拟供应商中断，随后两次模拟正常完成。"""

        calls.append((variant, repeat_index))
        interrupted = len(calls) == 1
        trace = run_root / f"{variant}.jsonl"
        patch = run_root / f"{variant}.patch"
        trace.write_text("interrupted" if interrupted else "completed", encoding="utf-8")
        patch.write_text("", encoding="utf-8")
        return {
            "instance_id": "a", "variant": variant, "repeat_index": repeat_index,
            "error": "Error code: 402 - Insufficient Balance" if interrupted else None,
            "success": not interrupted, "total_tokens": 10, "elapsed_seconds": 0.1,
            "trace_path": str(trace), "patch_path": str(patch),
        }

    fake_evaluator = SimpleNamespace(
        load_swebench=lambda key: {"a": SimpleNamespace(image="fake:latest")},
        audit_real_instance=lambda instance, run: {
            "instance_id": "a", "baseline_failed": True, "unstable_pass_to_pass": [],
        },
        evaluate_real_instance=evaluate,
    )
    monkeypatch.setattr(evals, "real_world", fake_evaluator)
    with pytest.raises(RuntimeError, match="供应商"):
        driver.main()
    driver.main()
    report = json.loads((run_root / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    assert len(report["rows"]) == 2
    assert calls == [("single_no_rag", 1), ("single_no_rag", 1), ("single_adaptive", 1)]
    assert len(report["provider_failures"]) == 1
    from pathlib import Path
    assert Path(report["provider_failures"][0]["trace_path"]).read_text(encoding="utf-8") == "interrupted"
