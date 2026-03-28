from app.utils.dates import parse_date  # noqa: F401
from app.utils.errors import api_error  # noqa: F401


def login_required(fn):
    def wrapper(*a, **kw):
        return fn(*a, **kw)
    return wrapper


@login_required
def get_user(user_id: int) -> dict:
    return {"id": user_id}


@login_required
def update_user(user_id: int, data: dict) -> dict:
    return {"id": user_id, **data}


@login_required
def delete_user(user_id: int) -> dict:
    return {"deleted": user_id}
