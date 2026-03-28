from app.models.base import AppModel


class User(AppModel):
    def __init__(self, user_id: int, name: str) -> None:
        self.user_id = user_id
        self.name = name

    def to_dict(self) -> dict:
        return {"id": self.user_id, "name": self.name}
