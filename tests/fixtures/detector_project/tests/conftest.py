import pytest


@pytest.fixture
def db():
    """In-memory DB for tests."""
    return {}


@pytest.fixture
def client(db):
    """Test client with DB injected."""
    return {"db": db}


@pytest.fixture
def admin_user():
    """Admin user for auth tests."""
    return {"id": 1, "role": "admin"}
