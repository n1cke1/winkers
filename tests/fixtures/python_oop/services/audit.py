"""Audit service — exercises self.<attr>.method() pattern."""

from repos.audit_log import AuditLogRepo
from repos.client import ClientRepo


class AuditService:
    def __init__(self, session):
        self.audit_repo = AuditLogRepo(session)
        self.client_repo = ClientRepo(session)
        self.plain_attr = session  # DI pattern — out of scope

    def log_event(self, data: dict) -> None:
        self.audit_repo.create(data)
        self.audit_repo.get_by_id(1)

    def record_client(self, email: str, data: dict) -> None:
        self.client_repo.create(data)
        self.client_repo.find_by_email(email)

    def plain_call(self):
        # DI-based attr — should NOT produce edges under MVP scope.
        self.plain_attr.commit()
