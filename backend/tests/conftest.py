import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.database.base import Base
from app.database.session import get_db
from app.main import app


@pytest.fixture(autouse=True)
def _disable_security():
    """The transport-security layer (loopback + device token) is exercised by
    test_security.py; the rest of the suite tests behaviour, so turn it off."""
    settings = get_settings()
    prev = settings.security_enabled
    settings.security_enabled = False
    yield
    settings.security_enabled = prev


@pytest.fixture
def db_session():
    """In-memory SQLite session, isolated per test.

    StaticPool keeps a single shared connection so the table created here is the
    same in-memory database the app sees when requests run on another thread.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session):
    """TestClient with the DB dependency pointed at the in-memory session."""
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
