"""
Tester för lösenordsskydd: session-tokens, login-endpoint och 401-skydd.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import app, get_db, _clear_status_cache
from app.auth import create_session_token, verify_session_token
from app.models import Base


@pytest.fixture(autouse=True)
def clear_cache():
    _clear_status_cache()
    yield
    _clear_status_cache()


@pytest.fixture
def db():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    MakeSession = sessionmaker(bind=eng, autoflush=True)
    with MakeSession() as session:
        yield session


@pytest.fixture
def client(db):
    """Klient utan session-override – testar det faktiska auth-lagret."""
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Session-token
# ---------------------------------------------------------------------------

class TestSessionToken:
    def test_skapat_token_är_giltigt(self):
        token = create_session_token()
        assert verify_session_token(token) is True

    def test_ogiltigt_token_ger_false(self):
        assert verify_session_token("inte-ett-riktigt-token") is False

    def test_tomt_token_ger_false(self):
        assert verify_session_token("") is False

    def test_manipulerat_token_ger_false(self):
        token = create_session_token()
        assert verify_session_token(token + "x") is False


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------

class TestLogin:
    def test_korrekt_losenord_ger_200_och_sätter_cookie(self, client, monkeypatch):
        monkeypatch.setattr("app.api.verify_password", lambda p: True)
        res = client.post("/auth/login", json={"password": "vadfansomhelst"})
        assert res.status_code == 200
        assert "session" in res.cookies

    def test_fel_losenord_ger_401(self, client, monkeypatch):
        monkeypatch.setattr("app.api.verify_password", lambda p: False)
        res = client.post("/auth/login", json={"password": "fel"})
        assert res.status_code == 401

    def test_fel_losenord_innehåller_felmeddelande(self, client, monkeypatch):
        monkeypatch.setattr("app.api.verify_password", lambda p: False)
        res = client.post("/auth/login", json={"password": "fel"})
        data = res.json()
        assert "detail" in data
        assert data["detail"]  # icke-tomt meddelande

    def test_korrekt_losenord_cookie_är_httponly(self, client, monkeypatch):
        monkeypatch.setattr("app.api.verify_password", lambda p: True)
        res = client.post("/auth/login", json={"password": "test"})
        assert res.status_code == 200
        set_cookie = res.headers.get("set-cookie", "")
        assert "HttpOnly" in set_cookie

    def test_korrekt_losenord_cookie_har_max_age(self, client, monkeypatch):
        monkeypatch.setattr("app.api.verify_password", lambda p: True)
        res = client.post("/auth/login", json={"password": "test"})
        set_cookie = res.headers.get("set-cookie", "")
        assert "Max-Age" in set_cookie


# ---------------------------------------------------------------------------
# Skydd av API-endpoints
# ---------------------------------------------------------------------------

class TestEndpointSkydd:
    def test_status_utan_session_ger_401(self, client):
        res = client.get("/api/status")
        assert res.status_code == 401

    def test_sync_utan_session_ger_401(self, client):
        res = client.post("/api/sync")
        assert res.status_code == 401

    def test_status_med_giltig_session_ger_200(self, client, monkeypatch):
        monkeypatch.setattr("app.api.verify_password", lambda p: True)
        login = client.post("/auth/login", json={"password": "test"})
        assert login.status_code == 200

        res = client.get("/api/status")
        assert res.status_code == 200

    def test_logout_rensar_cookie(self, client, monkeypatch):
        monkeypatch.setattr("app.api.verify_password", lambda p: True)
        client.post("/auth/login", json={"password": "test"})

        client.post("/auth/logout")

        res = client.get("/api/status")
        assert res.status_code == 401
