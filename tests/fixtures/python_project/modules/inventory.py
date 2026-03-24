"""Inventory module — fixture for tests."""

from modules.pricing import calculate_price


def check_stock(item_id: int, qty: int) -> bool:
    """Check if stock is available and compute cost."""
    cost = calculate_price(item_id, qty)
    return cost > 0


def reserve_items(item_id: int, qty: int) -> dict:
    """Reserve items in stock."""
    return {"item_id": item_id, "qty": qty, "reserved": True}
