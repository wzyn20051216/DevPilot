"""请求超时的跨模块验收测试。"""

import pytest

from schemas import RequestOptions
from service import create_client


def test_default_timeout_is_backward_compatible() -> None:
    assert create_client().timeout == 30


def test_custom_timeout_is_forwarded() -> None:
    assert create_client(RequestOptions(timeout=45)).timeout == 45


@pytest.mark.parametrize("timeout", [0, -1, 121])
def test_invalid_timeout_is_rejected(timeout: int) -> None:
    with pytest.raises(ValueError):
        create_client(RequestOptions(timeout=timeout))
