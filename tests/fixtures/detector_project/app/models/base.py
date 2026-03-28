"""Base model for all domain models."""


class AppModel:
    """Abstract base — all domain models inherit from this."""

    def to_dict(self) -> dict:
        raise NotImplementedError

    def validate(self) -> bool:
        return True
