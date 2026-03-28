"""Centralised error responses."""


def api_error(message: str, status_code: int = 400) -> tuple:
    return {"error": message}, status_code


def not_found(resource: str) -> tuple:
    return api_error(f"{resource} not found", 404)


def forbidden() -> tuple:
    return api_error("Forbidden", 403)
