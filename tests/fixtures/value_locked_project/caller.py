"""Calls into status with literal arguments."""
from status import can_transition, is_valid_status


def check_draft_to_sent() -> bool:
    return can_transition("draft", "sent")


def check_sent_to_paid() -> bool:
    return can_transition("sent", "paid")


def is_known(s: str) -> bool:
    return is_valid_status(s)


def is_known_paid() -> bool:
    return is_valid_status("paid")
