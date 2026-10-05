"""示例 HTTP 客户端。"""


class Client:
    """保存请求超时的最小客户端。"""

    def __init__(self, timeout: int) -> None:
        self.timeout = timeout
