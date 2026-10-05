"""代码索引一致性回归测试。"""

from pathlib import Path

import numpy as np
from pytest import MonkeyPatch

from backend.src.rag import code_index
from backend.src.rag.models import CodeChunk
from backend.src.tools.retrieval_tool import retrieve_code


def test_retrieve_rebuilds_index_after_source_change(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """源码变化后不得继续返回旧索引中的代码。"""

    source = tmp_path / "calculator.py"
    source.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    monkeypatch.setattr(
        code_index,
        "embed_texts",
        lambda texts: np.ones((len(texts), 2), dtype=np.float32),
    )
    monkeypatch.setattr(
        code_index,
        "embed_query",
        lambda query: np.ones(2, dtype=np.float32),
    )

    first = retrieve_code(str(tmp_path), "add")
    source.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    second = retrieve_code(str(tmp_path), "add")

    assert "a - b" in str(first[0]["content"])
    assert "a + b" in str(second[0]["content"])
    assert "a - b" not in str(second[0]["content"])


def test_retrieve_rebuilds_legacy_index_without_manifest(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """旧版索引缺少 manifest 时应自动升级，不能让用户手工清缓存。"""

    (tmp_path / "service.py").write_text(
        "def service():\n    return True\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        code_index,
        "embed_texts",
        lambda texts: np.ones((len(texts), 2), dtype=np.float32),
    )
    monkeypatch.setattr(
        code_index,
        "embed_query",
        lambda query: np.ones(2, dtype=np.float32),
    )

    index = code_index.CodeIndex(str(tmp_path))
    index.build()
    index.manifest_path.unlink()

    results = retrieve_code(str(tmp_path), "service")

    assert results
    assert index.manifest_path.is_file()


def test_hybrid_search_diversifies_files(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """同一文件的多个高分 chunk 不应挤掉其它相关文件。"""

    index = code_index.CodeIndex(str(tmp_path))
    index.chunks = [
        CodeChunk(
            id=str(position),
            file_path=path,
            language="python",
            symbol=f"symbol_{position}",
            start_line=1,
            end_line=1,
            content="pass",
            embedding_text="pass",
        )
        for position, path in enumerate(["tests/test_api.py", "tests/test_api.py", "api.py"])
    ]
    index.vectors = np.ones((3, 2), dtype=np.float32)
    monkeypatch.setattr(index, "vector_search", lambda query, top_k: [(0, 1.0), (1, 0.9), (2, 0.8)])
    monkeypatch.setattr(index, "bm25_search", lambda query, top_k: [(0, 3.0), (1, 2.0), (2, 1.0)])

    results = index.hybrid_search("api", top_k=2)

    assert [result["file_path"] for result in results] == ["tests/test_api.py", "api.py"]


def test_hybrid_weight_extremes_select_only_the_enabled_branch(tmp_path, monkeypatch):
    """! @brief 纯关键词/纯向量消融不得混入另一条路独有的候选。"""
    index = code_index.CodeIndex(str(tmp_path))
    index.chunks = [CodeChunk(id=str(i), file_path=f"{i}.py", language="python",
                             start_line=1, end_line=1, content="pass", embedding_text="pass")
                    for i in range(2)]
    index.vectors = np.ones((2, 2), dtype=np.float32)
    monkeypatch.setattr(index, "vector_search", lambda query, top_k: [(0, 1.0)])
    monkeypatch.setattr(index, "bm25_search", lambda query, top_k: [(1, 1.0)])
    assert [r["file_path"] for r in index.hybrid_search("q", vector_weight=0)] == ["1.py"]
    assert [r["file_path"] for r in index.hybrid_search("q", vector_weight=1)] == ["0.py"]


def test_text_chunking_rejects_non_progressing_windows(tmp_path):
    """! @brief 重叠不小于窗口会令循环停滞，必须在分块前拒绝。"""
    import pytest
    from backend.src.rag.chunker import chunk_text_file
    source = tmp_path / "long.ts"
    source.write_text("line\n" * 160, encoding="utf-8")
    for window, overlap in [(0, 0), (20, 20), (20, -1)]:
        with pytest.raises(ValueError):
            chunk_text_file(tmp_path, source, window, overlap)


def test_window_ablation_and_manifest_record_actual_parameters(tmp_path, monkeypatch):
    """! @brief 非默认分块必须实质改变长文件切分并保存可复核参数。"""
    import json
    source = tmp_path / "large.py"
    source.write_text("def long_function():\n" + "    value = 1\n" * 100, encoding="utf-8")
    monkeypatch.setattr(code_index, "embed_texts", lambda texts: np.ones((len(texts), 2)))
    index = code_index.CodeIndex(str(tmp_path))
    assert index.build()["chunks"] == 1
    assert index.build(chunk_lines=40, overlap=10, python_strategy="window")["chunks"] == 4
    manifest = json.loads(index.manifest_path.read_text(encoding="utf-8"))
    assert manifest["chunking"] == {"chunk_lines": 40, "overlap": 10, "python_strategy": "window"}
