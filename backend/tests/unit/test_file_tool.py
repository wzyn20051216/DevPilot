"""文件工具路径边界测试。"""

from pathlib import Path

import pytest

from backend.src.tools.file_tool import resolve_safe_path


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
