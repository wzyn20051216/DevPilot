"""客户端请求参数。"""

from dataclasses import dataclass


@dataclass
class RequestOptions:
    """调用方可选配置。"""

    timeout: int | None = None
