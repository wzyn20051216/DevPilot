"""! @brief 用真实终端颜色格式验证失败节点提取不会丢失。"""

from research.evals.pytest_output import strip_ansi_codes
from research.evals.real_world import _failed_pytest_nodes


def test_colored_failures_preserve_nodes_and_parameterization() -> None:
    """! @brief 彩色摘要必须与无颜色摘要得到相同失败节点。"""

    plain = "FAILED t.py::C::test_a[x] - AssertionError\nERROR t.py::test_b - ImportError\n"
    colored = (
        "\x1b[31mFAILED\x1b[0m t.py::\x1b[1mC::test_a[x]\x1b[0m - AssertionError\n"
        "\x1b[31m\x1b[1mERROR\x1b[0m t.py::test_b - ImportError\n"
    )
    assert strip_ansi_codes(colored) == plain
    assert _failed_pytest_nodes({"stdout": colored}) == ["t.py::C::test_a", "t.py::test_b"]
    assert _failed_pytest_nodes({"stdout": colored}, normalize=False) == [
        "t.py::C::test_a[x]", "t.py::test_b",
    ]


def test_non_failure_lines_do_not_become_failures() -> None:
    """! @brief 正文里的 FAILED 字样不能被当作终端失败摘要。"""

    output = "\x1b[32m3 passed\x1b[0m\n    assert status != 'FAILED t.py::test_a'\n"
    assert _failed_pytest_nodes({"stdout": output}) == []


def test_verifier_requests_failure_summary_even_when_repo_disables_it(monkeypatch, tmp_path) -> None:
    """! @brief 验证裁判覆盖仓库 -rN，避免有失败却无法提取节点的误判。"""

    from research.evals import real_world

    calls = []
    monkeypatch.setattr(real_world, "run_tests", lambda *args, **kwargs: calls.append(kwargs) or {})
    instance = real_world.SweBenchInstance("repo__repo-1", "repo/repo", "base", "issue", "", "", (), ())
    real_world._run_verification_targets(instance, tmp_path, ["tests/t.py"])
    assert calls[0]["targets"] == ["--color=no", "-r", "fE", "tests/t.py"]
