from decimal import Decimal

import pytest

from models import LineItem
from order_service import calculate_total
from pricing import discounted_subtotal


def test_discounted_subtotal_validates_discount() -> None:
    with pytest.raises(ValueError):
        discounted_subtotal(LineItem(Decimal("10"), 1, Decimal("1.1")))


def test_total_uses_discount_and_rounds_once() -> None:
    items = [
        LineItem(Decimal("10.005"), 2, Decimal("0.10")),
        LineItem(Decimal("3.335"), 1),
    ]
    assert calculate_total(items) == Decimal("21.34")
