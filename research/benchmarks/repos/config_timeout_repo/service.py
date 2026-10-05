"""客户端构造服务。"""

from client import Client


def build_client() -> Client:
    """构造默认客户端。"""

    return Client(timeout=10)
