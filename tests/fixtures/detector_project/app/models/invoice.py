from app.models.base import AppModel


class Invoice(AppModel):
    def __init__(self, invoice_id: int, amount_cents: int) -> None:
        self.invoice_id = invoice_id
        self.amount_cents = amount_cents

    def to_dict(self) -> dict:
        return {"id": self.invoice_id, "amount_cents": self.amount_cents}
