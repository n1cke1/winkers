from app.routes.users import login_required
from app.utils.errors import api_error
from app.utils.dates import parse_date


@login_required
def get_invoice(invoice_id: int) -> dict:
    return {"id": invoice_id}


@login_required
def create_invoice(data: dict) -> dict:
    due = parse_date(data.get("due_date", "2026-01-01"))
    return {"due": str(due)}


@login_required
def delete_invoice(invoice_id: int) -> dict:
    return {"deleted": invoice_id}
