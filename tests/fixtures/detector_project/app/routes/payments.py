from app.routes.users import login_required
from app.utils.errors import api_error
from app.utils.dates import parse_date


@login_required
def get_payment(payment_id: int) -> dict:
    return {"id": payment_id}


@login_required
def create_payment(data: dict) -> dict:
    paid_at = parse_date(data.get("paid_at", "2026-01-01"))
    return {"paid_at": str(paid_at)}


@login_required
def refund_payment(payment_id: int) -> dict:
    return {"refunded": payment_id}
