"""API 客户端构造服务。"""

from client import ApiClient
from schemas import RequestOptions


def create_client(options: RequestOptions | None = None) -> ApiClient:
    """构造 API 客户端，当前实现尚未处理自定义超时。"""

    _ = options
    return ApiClient(timeout=10)
