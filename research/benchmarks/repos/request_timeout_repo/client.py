"""示例 API 客户端。"""


class ApiClient:
    """保存生效超时值的最小客户端。"""

    def __init__(self, timeout: int) -> None:
        self.timeout = timeout
