"""! @brief 检索模型优先读取缓存，避免 MCP 每次都依赖外部网络。"""

from backend.src.rag import embedder


def test_cached_model_loads_without_network_check(monkeypatch):
    """! @brief 缓存命中时只进行离线构造，并在同进程复用实例。"""
    calls = []
    model = object()
    def construct(name, **kwargs):
        calls.append(kwargs)
        return model
    monkeypatch.setattr(embedder, "SentenceTransformer", construct)
    embedder.get_embedding_model.cache_clear()
    try:
        assert embedder.get_embedding_model() is model
        assert embedder.get_embedding_model() is model
        assert calls == [{"local_files_only": True}]
    finally:
        embedder.get_embedding_model.cache_clear()


def test_cache_miss_allows_first_model_download(monkeypatch):
    """! @brief 缺缓存时允许一次在线初始化，不删除或伪造原模型。"""
    calls = []
    model = object()
    def construct(name, **kwargs):
        calls.append(kwargs)
        if kwargs.get("local_files_only"):
            raise OSError("模型未缓存")
        return model
    monkeypatch.setattr(embedder, "SentenceTransformer", construct)
    embedder.get_embedding_model.cache_clear()
    try:
        assert embedder.get_embedding_model() is model
        assert calls == [{"local_files_only": True}, {}]
    finally:
        embedder.get_embedding_model.cache_clear()
