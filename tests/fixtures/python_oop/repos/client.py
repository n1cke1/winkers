"""Client repository — fixture."""


class ClientRepo:
    def __init__(self, session):
        self.session = session

    def create(self, data: dict) -> None:
        self.session.add(data)

    def find_by_email(self, email: str):
        return self.session.query(email)
