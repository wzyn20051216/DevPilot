"""超时配置回归测试。"""

from service import build_client


def test_build_client_uses_shared_default_timeout() -> None:
    assert build_client().timeout == 30
