import os

# Must be set BEFORE importing the app: config reads env vars at import time.
# Real env vars override .env, so tests never use your real DB or Gemini key.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET"] = "test-secret-key-that-is-at-least-32-bytes-long"
os.environ["LLM_PROVIDER"] = "fake"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def login(client):
    def _login(username: str = "alice", password: str = "password123") -> dict:
        client.post("/auth/signup", json={"username": username, "password": password})
        r = client.post("/auth/login", data={"username": username, "password": password})
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    return _login
