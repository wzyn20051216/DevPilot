"""! @brief 代码检索用 embedding 生成器。

本模块封装 sentence-transformers 模型加载和向量生成逻辑。
CodeIndex 构建索引时会调用 embed_texts() 批量生成代码片段向量，
查询时会调用 embed_query() 为用户问题生成同维度查询向量。
"""

from functools import lru_cache
import numpy as np
from sentence_transformers import SentenceTransformer

# 使用轻量级通用英文 embedding 模型；输出维度为 384。
MODEL_NAME = (
    "sentence-transformers/"
    "all-MiniLM-L6-v2"
)

@lru_cache(maxsize=1)
def get_embedding_model() -> SentenceTransformer:
    """! @brief 获取全局复用的 embedding 模型实例。

    sentence-transformers 模型加载较慢且占用内存，因此用 lru_cache
    保证同一进程内只加载一次。

    @return 已加载的 SentenceTransformer 模型。
    """
    return SentenceTransformer(
        MODEL_NAME
    )

def embed_texts(texts: list[str]) -> np.ndarray:
    """! @brief 批量生成文本 embedding 向量。

    生成的向量会做归一化，后续可以直接用点积计算余弦相似度。

    @param texts 待编码的文本列表。
    @return shape 为 ``(len(texts), 384)`` 的 float32 向量矩阵。
    """
    model = get_embedding_model()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return np.asarray(
        vectors,
        dtype=np.float32,
    )

def embed_query(
    query: str,
) -> np.ndarray:
    """! @brief 生成单条查询文本的 embedding 向量。

    @param query 用户输入的检索问题。
    @return shape 为 ``(384,)`` 的 float32 查询向量。
    """
    return embed_texts(
        [query]
    )[0]
