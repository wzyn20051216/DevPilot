"""calculator 模块的独立验收测试。"""

from calculator import add


def test_add_two_positive_integers() -> None:
    """add(2, 3) 应返回 5。"""

    assert add(2, 3) == 5
