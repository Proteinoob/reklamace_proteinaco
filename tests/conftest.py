import pytest
from datetime import datetime, timedelta, timezone

from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.database import Base

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def create_test_admin_token(username="test-admin", allowed_apps=None):
    """Create a JWT token for testing admin endpoints."""
    if allowed_apps is None:
        allowed_apps = ["reklamace"]
    payload = {
        "sub": "1",
        "username": username,
        "role": "admin",
        "allowed_apps": allowed_apps,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    from app.core.config import settings
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


@pytest.fixture(scope="function")
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session: Session):
    import app.core.database as db_module

    original_engine = db_module._engine
    original_session_local = db_module.SessionLocal

    db_module._engine = engine
    db_module.SessionLocal = TestingSessionLocal

    from app.main import app
    from app.core.database import get_sqlalchemy_session

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_sqlalchemy_session] = override_get_db

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    db_module._engine = original_engine
    db_module.SessionLocal = original_session_local


@pytest.fixture
def admin_client(db_session: Session):
    """Test client with admin JWT token in Authorization header."""
    import app.core.database as db_module

    original_engine = db_module._engine
    original_session_local = db_module.SessionLocal

    db_module._engine = engine
    db_module.SessionLocal = TestingSessionLocal

    from app.main import app
    from app.core.database import get_sqlalchemy_session

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_sqlalchemy_session] = override_get_db

    token = create_test_admin_token()

    with TestClient(app, raise_server_exceptions=False) as test_client:
        test_client.headers["Authorization"] = f"Bearer {token}"
        yield test_client

    app.dependency_overrides.clear()
    db_module._engine = original_engine
    db_module.SessionLocal = original_session_local
