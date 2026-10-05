import pytest

from inventory import remove_stock


def test_remove_stock_returns_remainder() -> None:
    assert remove_stock(8, 3) == 5


@pytest.mark.parametrize("quantity", [0, -1])
def test_remove_stock_rejects_non_positive_quantity(quantity: int) -> None:
    with pytest.raises(ValueError):
        remove_stock(8, quantity)


def test_remove_stock_rejects_insufficient_inventory() -> None:
    with pytest.raises(ValueError):
        remove_stock(2, 3)
