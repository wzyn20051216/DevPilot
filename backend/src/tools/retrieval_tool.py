"""! @brief 代码检索工具封装。

本模块把 RAG 层的 CodeIndex 包装成 Agent 可调用的工具函数。
registry.py 只负责工具注册和分发，真正的索引构建、索引加载和代码检索
逻辑都放在这里，避免工具注册表承担业务细节。
"""

from pathlib import Path
from typing import Any

from ..rag.code_index import CodeIndex


def build_code_index(
    repo_path: str,
) -> dict[str, int]:
    """! @brief 为指定仓库构建本地代码索引。

    该函数会扫描 repo_path 下的可索引源码文件，生成代码 chunk，
    再把 embedding 向量和 chunk 元数据写入仓库内的 .devpilot 目录。

    @param repo_path 代码仓库根目录路径。
    @return 包含 chunks 数量和向量 dimensions 的统计信息。
    @exception ValueError 当 repo_path 不是有效目录时抛出。
    @exception RuntimeError 当仓库没有可索引内容时由 CodeIndex.build() 抛出。
    """
    base_path = Path(repo_path).resolve()
    if not base_path.is_dir():
        raise ValueError(f"{repo_path} is not a valid directory.")

    index = CodeIndex(str(base_path))
    return index.build()


def retrieve_code(
    repo_path: str,
    query: str,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    """! @brief 从代码索引中检索与问题最相关的代码片段。

    如果本地还没有 .devpilot 索引，本函数会先自动构建一次索引；
    之后通过 CodeIndex.hybrid_search() 执行向量检索和 BM25 混合检索。

    @param repo_path 代码仓库根目录路径。
    @param query 用户问题或希望查找的代码语义描述。
    @param top_k 返回的最大结果数量。
    @return 检索结果列表，每项包含文件路径、符号名、行号、分数和代码内容。
    @exception ValueError 当 repo_path 不是有效目录，或 query 为空时抛出。
    """
    base_path = Path(repo_path).resolve()
    if not base_path.is_dir():
        raise ValueError(f"{repo_path} is not a valid directory.")

    query = query.strip()
    if not query:
        raise ValueError("query 不能为空。")

    index = CodeIndex(str(base_path))

    try:
        index.load()
    except FileNotFoundError:
        index.build()

    return index.hybrid_search(
        query=query,
        top_k=top_k,
    )
