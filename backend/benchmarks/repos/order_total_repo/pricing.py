from decimal import Decimal

from models import LineItem


def discounted_subtotal(item: LineItem) -> Decimal:
    """Calculate one line after applying its fractional discount."""

    return item.unit_price * item.quantity * item.discount
