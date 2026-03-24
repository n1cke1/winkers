"""Domain models — fixture for tests."""


class Price:
    """Represents a price record."""

    def __init__(self, item_id: int, amount: float) -> None:
        self.item_id = item_id
        self.amount = amount

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return {"item_id": self.item_id, "amount": self.amount}
