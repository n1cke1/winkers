from app.models.base import AppModel


class Payment(AppModel):
    def __init__(self, payment_id: int, amount_cents: int) -> None:
        self.payment_id = payment_id
        self.amount_cents = amount_cents

    def to_dict(self) -> dict:
        return {"id": self.payment_id, "amount_cents": self.amount_cents}
