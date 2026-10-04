"""增强策略组件测试：code_outline、编辑语法守卫与变体开关。"""

from pathlib import Path

import pytest

from backend.src.agents.single_developer_agent import (
    ENHANCED_ADDENDUM,
    SINGLE_DEVELOPER_PROMPT,
    SingleDeveloperAgent,
)
from backend.src.tools.code_outline import code_outline
from backend.src.tools.write_tool import replace_in_file, use_syntax_guard

SOURCE = '''import functools


class PersonName:
    """人名。"""

    def __init__(self, value):
        self.value = value

    @property
    def family(self) -> str:
        return self.value.split("^")[0]


def helper(a, b=1, *args, **kwargs):
    return a
'''


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(SOURCE, encoding="utf-8")
    return tmp_path


def test_outline_lists_classes_methods_and_functions(repo: Path) -> None:
    result = code_outline(str(repo), "pkg/mod.py")
    names = [item["name"] for item in result["outline"]]
    assert names == ["PersonName", "helper"]
    cls = result["outline"][0]
    assert [m["name"] for m in cls["methods"]] == ["__init__", "family"]
    assert cls["methods"][1]["decorators"] == ["property"]
    assert result["outline"][1]["signature"] == "def helper(a, b=1, *args, **kwargs)"


def test_outline_symbol_returns_numbered_source_with_decorator(repo: Path) -> None:
    result = code_outline(str(repo), "pkg/mod.py", symbol="PersonName.family")
    assert result["found"] is True
    assert result["source"].splitlines()[0].endswith("@property")
    assert "return self.value" in result["source"]


def test_outline_missing_symbol_and_path_escape(repo: Path) -> None:
    assert code_outline(str(repo), "pkg/mod.py", symbol="Nope")["found"] is False
    with pytest.raises(ValueError):
        code_outline(str(repo), "../outside.py")


def test_syntax_guard_rejects_broken_edit_and_keeps_file(repo: Path) -> None:
    target = repo / "pkg" / "mod.py"
    with use_syntax_guard(True), pytest.raises(ValueError, match="语法错误"):
        replace_in_file(str(repo), "pkg/mod.py", "return a\n", "return (a\n")
    assert target.read_text(encoding="utf-8") == SOURCE


def test_syntax_guard_is_off_by_default(repo: Path) -> None:
    result = replace_in_file(str(repo), "pkg/mod.py", "return a\n", "return (a\n")
    assert result["changed"] is True


def test_enhanced_switch_only_adds_documented_differences(repo: Path) -> None:
    base = SingleDeveloperAgent(str(repo), enable_rag=False)
    enhanced = SingleDeveloperAgent(str(repo), enable_rag=False, enhanced=True)
    assert "code_outline" not in base.allowed_tools
    assert enhanced.allowed_tools - base.allowed_tools == {"code_outline"}
    assert base.system_prompt == SINGLE_DEVELOPER_PROMPT
    assert enhanced.system_prompt == SINGLE_DEVELOPER_PROMPT + ENHANCED_ADDENDUM


def test_test_nodes_accepts_list_and_json_string() -> None:
    from backend.src.evals.real_world import _test_nodes

    assert _test_nodes(["a::b"]) == ("a::b",)
    assert _test_nodes('["a::b", "c::d"]') == ("a::b", "c::d")
    assert _test_nodes(None) == ()


def test_overlay_writes_only_regular_files_inside_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import subprocess
    import tarfile

    from backend.src.evals import real_world

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in (("pkg/_version.py", b"v = '1'\n"), ("../escape.txt", b"x")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("pkg/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        archive.addfile(link)

    monkeypatch.setattr(
        real_world.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, buffer.getvalue(), b""),
    )
    instance = real_world.SweBenchInstance("o__p-1", "o/p", "", "", "", "", (), ())
    workspace = tmp_path / "ws"
    workspace.mkdir()
    assert real_world._overlay_build_artifacts(instance, workspace) == 1
    assert (workspace / "pkg" / "_version.py").read_text() == "v = '1'\n"
    assert not (tmp_path / "escape.txt").exists()
    assert not (workspace / "pkg" / "link").exists()
