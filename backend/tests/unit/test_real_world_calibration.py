"""! @brief 防止测试收集/内部错误被环境失败豁免掩盖的裁判回归。"""

from pathlib import Path

import pytest

from backend.src.evals import real_world


def _instance() -> real_world.SweBenchInstance:
    """! @brief 提供不调用网络、模型或 Docker 的独立校准场景。"""
    return real_world.SweBenchInstance(
        instance_id="owner__repo-1", repo="owner/repo", base_commit="base",
        problem_statement="bug", gold_patch="", test_patch="",
        fail_to_pass=("tests/test_bug.py::test_bug",), pass_to_pass=(),
    )


@pytest.mark.parametrize("returncode, expected", [(1, True), (2, False), (3, False)])
def test_reference_patch_cannot_pass_on_collection_or_internal_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, returncode: int, expected: bool,
) -> None:
    """! @brief 参考修复仍可容忍已知无关失败，但收集/内部错误必须使校准无效。"""
    monkeypatch.setattr(real_world, "create_real_workspace", lambda *_: tmp_path)
    monkeypatch.setattr(real_world, "_apply_patch", lambda *_: None)
    outcomes = iter([
        {"passed": False, "returncode": 1,
         "stdout": "FAILED tests/test_bug.py::test_bug - AssertionError", "stderr": ""},
        {"passed": False, "returncode": returncode,
         "stdout": "ERROR tests/test_env.py - Read-only file system", "stderr": ""},
    ])
    monkeypatch.setattr(real_world, "_run_verification_targets", lambda *_: next(outcomes))
    audit = real_world.audit_real_instance(_instance(), "run")
    assert audit["gold_passed"] is expected
    assert audit["baseline_failed"] is expected


@pytest.mark.parametrize("returncode, expected", [(1, True), (2, False), (3, False)])
def test_candidate_error_is_not_hidden_by_environment_exclusion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, returncode: int, expected: bool,
) -> None:
    """! @brief 同一环境节点已列入豁免时，exit=2/3 也不能把候选标为通过。"""
    monkeypatch.setattr(real_world, "create_real_workspace", lambda *_: tmp_path)
    monkeypatch.setattr(real_world, "_apply_patch", lambda *_: None)
    monkeypatch.setattr(real_world, "_run_verification_targets", lambda *_: {
        "passed": False, "returncode": returncode,
        "stdout": "ERROR tests/test_env.py - Read-only file system", "stderr": "",
    })
    verdict = real_world.verify_real_patch(
        _instance(), "", "run", "candidate", excluded_targets=("tests/test_env.py",),
    )
    assert verdict["passed"] is expected
