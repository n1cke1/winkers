"""Audit log repository — fixture for self.<attr>.method() resolver."""


class AuditLogRepo:
    def __init__(self, session):
        self.session = session

    def create(self, data: dict) -> None:
        self.session.add(data)

    def get_by_id(self, id: int):
        return self.session.get(id)
