"""文件工具路径边界测试。"""

from pathlib import Path

import pytest

from backend.src.tools.file_tool import read_files, resolve_safe_path, search_code
from backend.src.tools.write_tool import replace_in_file


def test_safe_path_inside_repo(tmp_path: Path) -> None:
    """仓库内部的相对路径应被解析为仓库下的绝对路径。"""

    repo = tmp_path / "repo"
    repo.mkdir()
    target = resolve_safe_path(repo, "src/main.py")
    assert repo.resolve() in target.parents


def test_safe_path_rejects_escape(tmp_path: Path) -> None:
    """包含 ``..`` 的路径不能逃出仓库根目录。"""

    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(ValueError, match="path traversal"):
        _ = resolve_safe_path(repo, "../secret.txt")


def test_read_file_range_includes_source_line_numbers(tmp_path: Path) -> None:
    """大型文件可按行读取，便于模型构造稳定的局部修改。"""

    source = tmp_path / "module.py"
    source.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    assert read_files(tmp_path, "module.py", start_line=2, end_line=3) == (
        "2: two\n3: three"
    )


def test_search_code_returns_line_numbers(tmp_path: Path) -> None:
    """搜索结果应带行号，避免模型重新读取整份大文件。"""

    (tmp_path / "module.py").write_text("first\ntarget value\n", encoding="utf-8")

    results = search_code(str(tmp_path), "target")

    assert results[0]["matching_lines"] == [
        {"line_number": 2, "text": "target value"}
    ]


def test_replace_in_file_requires_unique_anchor(tmp_path: Path) -> None:
    """局部替换只接受唯一锚点，并通过原子写入保存结果。"""

    source = tmp_path / "module.py"
    source.write_text("before\nold value\nafter\n", encoding="utf-8")

    result = replace_in_file(str(tmp_path), "module.py", "old value", "new value")

    assert result["changed"] is True
    assert "new value" in source.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="匹配 0 次"):
        replace_in_file(str(tmp_path), "module.py", "missing", "x")
