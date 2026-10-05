from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class LineItem:
    unit_price: Decimal
    quantity: int
    discount: Decimal = Decimal("0")
