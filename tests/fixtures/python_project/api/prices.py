"""Prices API — fixture for tests."""

from modules.pricing import calculate_price


def get_price(item_id: int, qty: int) -> dict:
    """Return price info for an item."""
    total = calculate_price(item_id, qty)
    return {"item_id": item_id, "qty": qty, "total": total}
