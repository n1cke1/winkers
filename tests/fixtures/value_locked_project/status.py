"""Status machine — fixture for value_locked detection."""

VALID_STATUSES = {"draft", "sent", "viewed", "paid", "void"}

TRANSITIONS = {
    "draft": {"sent"},
    "sent": {"viewed", "paid"},
    "viewed": {"paid"},
    "paid": {"void"},
    "void": set(),
}


def can_transition(current: str, target: str) -> bool:
    """Check status transition is allowed."""
    return target in TRANSITIONS.get(current, set())


def is_valid_status(status: str) -> bool:
    """Check status string is in the valid set."""
    return status in VALID_STATUSES
