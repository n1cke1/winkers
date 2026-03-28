"""Date utilities — wrapper around datetime."""
from datetime import datetime


def parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value)


def format_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")
