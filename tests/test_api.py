"""
Tester för FastAPI-endpoints (app/api.py).
Databasen är en in-memory SQLite-instans per test.
Synken är mockad – inga nätverksanrop.
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import app, get_db, _clear_status_cache
from app.auth import require_session
from app.models import Appearance, Base, Match, Player, SyncLog
from app.sync import SyncResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
def api_client(db):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_session] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Hjälpfunktioner
# ---------------------------------------------------------------------------

def add_match(db, match_id, team, kickoff, status="played"):
    db.add(Match(
        match_id=match_id, team=team, competition_id=100,
        kickoff=kickoff, status=status, raw={},
    ))


def add_appearance(db, match_id, player_id, name="Testspelare"):
    db.add(Appearance(match_id=match_id, player_id=player_id, player_name=name))


def add_player(db, player_id, name="Spelare", shirt_no="9"):
    db.add(Player(
        player_id=player_id, name=name, shirt_no=shirt_no,
        last_seen=datetime(2026, 1, 1),
    ))


def make_sync_result(ok=True, matches_added=0, warnings=None) -> SyncResult:
    return SyncResult(
        ok=ok,
        matches_added=matches_added,
        warnings=warnings or [],
        started_at=datetime(2026, 8, 27, 10, 0),
        finished_at=datetime(2026, 8, 27, 10, 1),
        log_id=1,
    )


# ---------------------------------------------------------------------------
# GET /api/status
# ---------------------------------------------------------------------------

class TestGetStatus:
    def test_tom_db_returnerar_tomma_grupper(self, api_client):
        response = api_client.get("/api/status")

        assert response.status_code == 200
        data = response.json()
        assert data["grupper"]["maste_sta_over"] == []
        assert data["grupper"]["tillgangliga"] == []
        assert data["grupper"]["lasta"] == []
        assert data["senaste_sync"] is None
        assert data["rakningar"] == {"maste_sta_over": 0, "tillgangliga": 0, "lasta": 0}

    def test_trupp_spelare_utan_matcher_ar_tillganglig_med_markering(self, db, api_client):
        add_player(db, 99, "Ny Spelare", "3")
        db.flush()

        response = api_client.get("/api/status")
        data = response.json()

        tillg = data["grupper"]["tillgangliga"]
        assert len(tillg) == 1
        row = tillg[0]
        assert row["player_id"] == 99
        assert row["namn"] == "Ny Spelare"
        assert row["trojnummer"] == "3"
        assert row["maste_spela_b_forst"] is True
        assert row["matcher_kvar"] == 2
        assert row["lock_orsak"] is None
        assert data["rakningar"]["tillgangliga"] == 1

    def test_spelare_med_b_match_ar_tillganglig(self, db, api_client):
        add_match(db, 1, "B", datetime(2020, 1, 1))
        add_player(db, 42, "Kalle", "7")
        add_appearance(db, 1, 42, "Kalle")
        db.flush()

        response = api_client.get("/api/status")
        data = response.json()

        tillg = data["grupper"]["tillgangliga"]
        assert len(tillg) == 1
        assert tillg[0]["maste_spela_b_forst"] is False
        assert tillg[0]["matcher_kvar"] == 2

    def test_tva_a_matcher_i_rad_hamnar_i_maste_sta_over(self, db, api_client):
        add_match(db, 1, "B", datetime(2020, 1, 1))
        add_match(db, 2, "A", datetime(2020, 1, 10))
        add_match(db, 3, "A", datetime(2020, 1, 20))
        add_player(db, 42, "Kalle", "7")
        for mid in (1, 2, 3):
            add_appearance(db, mid, 42, "Kalle")
        db.flush()

        response = api_client.get("/api/status")
        data = response.json()

        assert data["rakningar"]["maste_sta_over"] == 1
        assert data["rakningar"]["tillgangliga"] == 0
        row = data["grupper"]["maste_sta_over"][0]
        assert row["player_id"] == 42
        assert row["matcher_kvar"] == 0
        assert row["maste_spela_b_forst"] is False
        assert row["lock_orsak"] is None

    def test_last_spelare_hamnar_i_lasta(self, db, api_client):
        add_match(db, 1, "A", datetime(2020, 1, 1))
        add_player(db, 55, "Erik", "10")
        add_appearance(db, 1, 55, "Erik")
        db.flush()

        response = api_client.get("/api/status")
        data = response.json()

        assert data["rakningar"]["lasta"] == 1
        row = data["grupper"]["lasta"][0]
        assert row["player_id"] == 55
        assert row["lock_orsak"] is not None
        assert row["lock_datum"] is not None
        assert row["matcher_kvar"] is None

    def test_tre_grupper_med_korrekt_rakningar(self, db, api_client):
        # Matcher som används
        add_match(db, 1, "B", datetime(2020, 1, 1))
        add_match(db, 2, "A", datetime(2020, 1, 10))
        add_match(db, 3, "A", datetime(2020, 1, 20))

        # Måste stå över: spelare 10 med 2 A-matcher i rad (consecutive_a=2)
        add_player(db, 10, "MåsteStåÖver", "1")
        for mid in (1, 2, 3):
            add_appearance(db, mid, 10, "MåsteStåÖver")

        # Tillgänglig: spelare 20, bara B-match → consecutive_a=0, matches_left=2
        add_player(db, 20, "Tillgänglig", "2")
        add_appearance(db, 1, 20, "Tillgänglig")

        # Låst: spelare 30, spelade A utan B
        add_player(db, 30, "Låst", "3")
        add_appearance(db, 2, 30, "Låst")

        # Trupp utan matcher: spelare 40
        add_player(db, 40, "NyTrupp", "4")

        db.flush()

        response = api_client.get("/api/status")
        data = response.json()

        assert data["rakningar"]["maste_sta_over"] == 1
        assert data["rakningar"]["tillgangliga"] == 2   # Tillgänglig + NyTrupp
        assert data["rakningar"]["lasta"] == 1

    def test_tillgangliga_sorteras_med_lagst_matcher_kvar_forst(self, db, api_client):
        # Spelare A: 1 A-match → matches_left=1
        add_match(db, 1, "B", datetime(2020, 1, 1))
        add_match(db, 2, "A", datetime(2020, 1, 10))
        add_player(db, 10, "SpelareA", "1")
        add_appearance(db, 1, 10, "SpelareA")
        add_appearance(db, 2, 10, "SpelareA")

        # Spelare B: bara B-match → matches_left=2
        add_player(db, 20, "SpelareB", "2")
        add_appearance(db, 1, 20, "SpelareB")

        db.flush()

        response = api_client.get("/api/status")
        data = response.json()

        tillg = data["grupper"]["tillgangliga"]
        assert tillg[0]["player_id"] == 10   # 1 kvar → först
        assert tillg[1]["player_id"] == 20   # 2 kvar → sedan

    def test_senaste_lyckade_sync_visas(self, db, api_client):
        db.add(SyncLog(
            started_at=datetime(2026, 8, 27, 10, 0),
            finished_at=datetime(2026, 8, 27, 10, 5),
            matches_added=3,
            warnings=[],
            ok=True,
        ))
        db.flush()

        response = api_client.get("/api/status")
        data = response.json()

        assert data["senaste_sync"] is not None
        assert "2026-08-27" in data["senaste_sync"]

    def test_misslyckad_sync_visas_inte_i_senaste_sync(self, db, api_client):
        db.add(SyncLog(
            started_at=datetime(2026, 8, 27, 10, 0),
            finished_at=datetime(2026, 8, 27, 10, 1),
            matches_added=0,
            warnings=["fel"],
            ok=False,
        ))
        db.flush()

        response = api_client.get("/api/status")
        data = response.json()

        assert data["senaste_sync"] is None

    def test_varningar_fran_motorn_inkluderas(self, db, api_client):
        # A- och B-match med exakt samma kickoff → varning från motorn
        dt = datetime(2020, 1, 1, 19, 0)
        add_match(db, 1, "A", dt)
        add_match(db, 2, "B", dt)
        add_player(db, 42, "Spelare", "7")
        add_appearance(db, 1, 42, "Spelare")
        add_appearance(db, 2, 42, "Spelare")
        db.flush()

        response = api_client.get("/api/status")
        data = response.json()

        assert len(data["varningar"]) > 0

    def test_cache_returnerar_samma_svar(self, api_client):
        response1 = api_client.get("/api/status")
        response2 = api_client.get("/api/status")

        assert response1.json() == response2.json()

    def test_a_match_ids_inkluderas_i_rad(self, db, api_client):
        add_match(db, 1, "B", datetime(2020, 1, 1))
        add_match(db, 2, "A", datetime(2020, 1, 10))
        add_player(db, 42, "Kalle", "7")
        add_appearance(db, 1, 42, "Kalle")
        add_appearance(db, 2, 42, "Kalle")
        db.flush()

        response = api_client.get("/api/status")
        data = response.json()

        row = data["grupper"]["tillgangliga"][0]
        assert 2 in row["a_match_ids"]


# ---------------------------------------------------------------------------
# POST /api/sync
# ---------------------------------------------------------------------------

class TestPostSync:
    def test_sync_kors_och_returnerar_ok(self, api_client, monkeypatch):
        monkeypatch.setattr("app.api.run_sync", lambda db, client: make_sync_result(
            ok=True, matches_added=5,
        ))

        response = api_client.post("/api/sync")

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["matcher_tillagda"] == 5
        assert data["varningar"] == []
        assert "startad" in data
        assert "klar" in data

    def test_misslyckad_sync_returnerar_ok_false(self, api_client, monkeypatch):
        monkeypatch.setattr("app.api.run_sync", lambda db, client: make_sync_result(
            ok=False, warnings=["Synken avbröts med fel: nätverksfel"],
        ))

        response = api_client.post("/api/sync")

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is False
        assert len(data["varningar"]) > 0

    def test_sync_bygger_om_cachen(self, db, api_client, monkeypatch):
        monkeypatch.setattr("app.api.run_sync", lambda db, client: make_sync_result())

        # Hämta status (tom cache → byggs)
        before = api_client.get("/api/status").json()
        assert before["rakningar"]["tillgangliga"] == 0

        # Lägg till en spelare i DB, kör sync → cache byggs om
        add_player(db, 99, "Ny", "1")
        db.flush()
        api_client.post("/api/sync")

        after = api_client.get("/api/status").json()
        assert after["rakningar"]["tillgangliga"] == 1

    def test_sync_varningar_inkluderas_i_svar(self, api_client, monkeypatch):
        monkeypatch.setattr("app.api.run_sync", lambda db, client: make_sync_result(
            warnings=["Avbruten match utan resultat"],
        ))

        response = api_client.post("/api/sync")
        data = response.json()

        assert "Avbruten match utan resultat" in data["varningar"]
