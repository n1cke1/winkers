"""Pricing module — fixture for tests."""


def get_base_price(item_id: int) -> float:
    """Return base price for an item."""
    prices = {1: 10.0, 2: 20.0, 3: 5.0}
    return prices.get(item_id, 0.0)


def apply_discount(price: float, pct: float) -> float:
    """Apply a percentage discount."""
    return price * (1 - pct / 100)


def calculate_price(item_id: int, qty: int) -> float:
    """Calculate final price with all applicable discounts."""
    base = get_base_price(item_id)
    if qty > 100:
        base = apply_discount(base, 10)
    return base * qty
