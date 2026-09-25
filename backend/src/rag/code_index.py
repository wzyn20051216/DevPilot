"""! @brief 代码仓库 RAG 索引。

CodeIndex 负责把一个代码仓库转换为本地可复用索引：
- 调用 chunk_repository() 将源码切分为 CodeChunk；
- 调用 embed_texts() 生成向量矩阵；
- 将 chunks.json 和 vectors.npy 保存到仓库的 .devpilot 目录；
- 提供向量检索、BM25 检索和 Reciprocal Rank Fusion 混合检索。
"""

import json
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from ..rag.chunker import (
    chunk_repository,
)
from ..rag.embedder import (
    embed_query,
    embed_texts,
)
from ..rag.models import (
    CodeChunk,
)
INDEX_DIR_NAME = ".devpilot"

class CodeIndex:
    """! @brief 面向单个仓库的代码索引管理器。

    一个 CodeIndex 实例对应一个代码仓库。实例可以构建索引、加载索引，
    并基于已有索引执行语义检索和关键词检索。
    """

    def __init__(
        self,
        repo_path: str,
    ) -> None:
        """! @brief 初始化代码索引路径和运行时缓存。

        @param repo_path 待索引代码仓库的根目录路径。
        """

        self.repo = Path(
            repo_path
        ).resolve()

        self.index_dir = (
            self.repo
            / INDEX_DIR_NAME
        )

        self.chunks_path = (
            self.index_dir
            / "chunks.json"
        )

        self.vectors_path = (
            self.index_dir
            / "vectors.npy"
        )

        self.chunks: list[
            CodeChunk
        ] = []

        self.vectors: (
            np.ndarray | None
        ) = None
    def build(
        self,
    ) -> dict[str, int]:
        """! @brief 构建并持久化代码仓库索引。

        构建流程包括代码分块、embedding 生成、chunk 元数据写入
        chunks.json，以及向量矩阵写入 vectors.npy。

        @return 包含 chunk 数量和向量维度的统计信息。
        @exception RuntimeError 当仓库中没有可索引内容时抛出。
        """

        chunks = (
            chunk_repository(
                str(self.repo)
            )
        )

        if not chunks:
            raise RuntimeError(
                "代码仓库中没有可索引内容"
            )

        vectors = embed_texts(
            [
                chunk.embedding_text
                for chunk in chunks
            ]
        )

        self.index_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.chunks_path.write_text(
            json.dumps(
                [
                    chunk.model_dump()
                    for chunk in chunks
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        np.save(
            self.vectors_path,
            vectors,
        )

        self.chunks = chunks
        self.vectors = vectors

        return {
            "chunks": len(chunks),
            "dimensions": (
                int(vectors.shape[1])
            ),
        }
    def load(
        self,
    ) -> None:
        """! @brief 从本地 .devpilot 目录加载已有索引。

        加载后会填充 self.chunks 和 self.vectors，供后续检索复用。

        @exception FileNotFoundError 当 chunks.json 或 vectors.npy 不存在时抛出。
        """

        if (
            not self.chunks_path.exists()
            or not self.vectors_path.exists()
        ):
            raise FileNotFoundError(
                "代码索引不存在，请先 build"
            )

        raw_chunks = json.loads(
            self.chunks_path.read_text(
                encoding="utf-8"
            )
        )

        self.chunks = [
            CodeChunk.model_validate(
                item
            )
            for item in raw_chunks
        ]

        self.vectors = np.load(
            self.vectors_path
        )
    def vector_search(
        self,
        query: str,
        top_k: int = 10,
    ) -> list[
        tuple[int, float]
    ]:
        """! @brief 使用向量相似度检索相关代码片段。

        查询文本会先转换为 embedding，然后与索引向量做点积排序。
        因为 embedding 已归一化，点积等价于余弦相似度。

        @param query 用户输入的检索问题。
        @param top_k 返回的候选数量。
        @return ``(chunk_index, score)`` 列表，按分数从高到低排列。
        """

        if self.vectors is None:
            self.load()

        assert self.vectors is not None

        query_vector = embed_query(
            query
        )

        # 索引和查询向量都已在 embedder 中做 L2 归一化，因此矩阵乘法
        # 得到的点积就是余弦相似度，无需再逐条调用 cosine_similarity。
        scores = (
            self.vectors
            @ query_vector
        )

        # argsort 默认从小到大，[::-1] 翻转为降序，再截取 top_k。
        # 返回的是 chunk 在 self.chunks 中的位置，不是 CodeChunk.id。
        indices = np.argsort(
            scores
        )[::-1][:top_k]

        return [
            (
                int(index),
                float(scores[index]),
            )
            for index in indices
        ]
    @staticmethod
    def tokenize(
        text: str,
    ) -> list[str]:
        """! @brief 将文本拆成 BM25 使用的简单 token 列表。

        这里采用轻量规则切分，避免为代码检索引入额外分词依赖。

        @param text 待分词文本。
        @return 小写化后的 token 列表。
        """

        return (
            text.lower()
            .replace("(", " ")
            .replace(")", " ")
            .replace(".", " ")
            .replace("_", " ")
            .replace("/", " ")
            .split()
        )

    def bm25_search(
        self,
        query: str,
        top_k: int = 10,
    ) -> list[
        tuple[int, float]
    ]:
        """! @brief 使用 BM25 关键词相关性检索代码片段。

        BM25 更擅长匹配函数名、文件名、变量名等精确关键词，
        可以弥补纯向量检索对符号级文本不够敏感的问题。

        @param query 用户输入的检索问题。
        @param top_k 返回的候选数量。
        @return ``(chunk_index, score)`` 列表，按分数从高到低排列。
        """

        if not self.chunks:
            self.load()

        corpus = [
            self.tokenize(
                chunk.embedding_text
            )
            for chunk in self.chunks
        ]

        bm25 = BM25Okapi(
            corpus
        )

        scores = bm25.get_scores(
            self.tokenize(query)
        )

        indices = np.argsort(
            scores
        )[::-1][:top_k]

        return [
            (
                int(index),
                float(scores[index]),
            )
            for index in indices
        ]
    def hybrid_search(
        self,
        query: str,
        top_k: int = 8,
    ) -> list[
        dict[str, object]
    ]:
        """! @brief 执行向量检索和 BM25 的混合检索。

        当前实现先分别取向量检索和 BM25 的前 20 个候选，
        再使用 Reciprocal Rank Fusion（RRF）合并排名。

        @param query 用户输入的检索问题。
        @param top_k 最终返回的结果数量。
        @return 包含文件路径、符号、行号、分数和内容的结果列表。
        """

        if not self.chunks:
            self.load()

        vector_results = (
            self.vector_search(
                query,
                top_k=20,
            )
        )

        bm25_results = (
            self.bm25_search(
                query,
                top_k=20,
            )
        )

        scores: dict[
            int,
            float,
        ] = {}

        # RRF 的平滑常数。数值越大，排名差异对最终分数的影响越温和。
        k = 60

        # RRF 只使用“名次”而不直接相加两种原始分数，因为向量相似度与
        # BM25 分数不在同一量纲。某个 chunk 两边都靠前时会自然累加得分。
        for rank, (
            index,
            _,
        ) in enumerate(
            vector_results,
            start=1,
        ):

            scores[index] = (
                scores.get(
                    index,
                    0.0,
                )
                + 1.0 / (k + rank)
            )

        for rank, (
            index,
            _,
        ) in enumerate(
            bm25_results,
            start=1,
        ):

            scores[index] = (
                scores.get(
                    index,
                    0.0,
                )
                + 1.0 / (k + rank)
            )

        # scores.items() 形如 (chunk_index, rrf_score)，按融合分数降序截断。
        ranked = sorted(
            scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:top_k]

        results: list[
            dict[str, object]
        ] = []

        for index, score in ranked:

            chunk = self.chunks[
                index
            ]

            results.append({
                "file_path": (
                    chunk.file_path
                ),
                "symbol": (
                    chunk.symbol
                ),
                "start_line": (
                    chunk.start_line
                ),
                "end_line": (
                    chunk.end_line
                ),
                "score": score,
                "content": (
                    chunk.content
                ),
            })

        return results
