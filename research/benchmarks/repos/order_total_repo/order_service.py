from decimal import Decimal

from models import LineItem


def calculate_total(items: list[LineItem]) -> Decimal:
    """Calculate the payable amount for all line items."""

    return sum((item.unit_price * item.quantity for item in items), Decimal("0"))
