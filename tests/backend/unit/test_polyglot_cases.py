"""! @brief polyglot benchmark 基础设施的离线单元测试。

这些测试不依赖 Docker：通过 monkeypatch 拦截 run_in_sandbox，验证新 case 的
数据加载、command 透传、polyglot profile 切换、白名单扩展和旧 case 兼容性。
"""

from typing import Any

from pytest import MonkeyPatch

from backend.src.config import settings
from research.evals.dataset import load_benchmark_cases, load_case
from backend.src.sandbox import docker_runner
from backend.src.tools import test_tool

_POLYGLOT_CASE_IDS = {"ts_report", "java_inventory", "py_pipeline"}


def test_polyglot_cases_load_with_required_fields() -> None:
    """三个新 case 应能加载，且语言/仓库/验证字段齐全合法。"""

    expected_command = {
        "ts_report": ["npx", "tsx", "--test", "tests/report.test.ts"],
        "java_inventory": ["java", "src/inventory/InventoryTest.java"],
    }
    for case_id in _POLYGLOT_CASE_IDS:
        case = load_case(case_id)
        assert case.repo_fixture
        assert case.language in {"python", "typescript", "javascript", "java"}
        if case_id in expected_command:
            assert case.verification_command == expected_command[case_id]
        else:
            # py_pipeline 走 pytest 原路径，verification_command 保持 None。
            assert case.verification_command is None
            assert case.verification_target == "tests/test_pipeline.py"
            assert case.language == "python"


def test_run_tests_passes_command_argv_and_switches_polyglot_profile(
    monkeypatch: MonkeyPatch,
) -> None:
    """run_tests(command=...) 应透传 argv 并切换到 polyglot 镜像。"""

    captured: dict[str, Any] = {}
    active_images: list[str | None] = []

    def fake_sandbox(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        profile = docker_runner._ACTIVE_PROFILE.get()
        active_images.append(profile.image if profile else None)
        return {"returncode": 0, "timed_out": False, "stdout": "", "stderr": ""}

    monkeypatch.setattr(test_tool, "run_in_sandbox", fake_sandbox)
    command = ["npx", "tsx", "--test", "tests/report.test.ts"]
    result = test_tool.run_tests(".", command=command, timeout=30)

    assert captured["argv"] == command
    assert captured["timeout"] == 30
    assert active_images == [settings.sandbox_image_polyglot]
    assert result["passed"] is True


def test_allowed_programs_include_polyglot_and_reject_dangerous() -> None:
    """白名单应包含 node/npx/javac/java，同时仍拒绝 bash/rm。"""

    assert {"node", "npx", "javac", "java"} <= docker_runner.ALLOWED_PROGRAMS
    assert "bash" not in docker_runner.ALLOWED_PROGRAMS
    assert "rm" not in docker_runner.ALLOWED_PROGRAMS


def test_legacy_cases_remain_python_default() -> None:
    """旧 9 个 case 反序列化后应保持 language=python 且 verification_command=None。"""

    cases = load_benchmark_cases()
    legacy = [case for case in cases if case.id not in _POLYGLOT_CASE_IDS]
    assert len(legacy) == 9
    assert all(case.language == "python" for case in legacy)
    assert all(case.verification_command is None for case in legacy)
