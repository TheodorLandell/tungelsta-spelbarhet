"""
Tester för manuella overrides: POST /api/overrides och DELETE /api/overrides/{player_id}.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import app, get_db, _clear_status_cache
from app.auth import require_session
from app.models import Base, Match, Player, Appearance


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
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_session] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_player(db, player_id, name="Spelare", shirt_no="9"):
    db.add(Player(player_id=player_id, name=name, shirt_no=shirt_no, last_seen=datetime(2026, 1, 1)))


def add_match(db, match_id, team, kickoff, status="played"):
    db.add(Match(match_id=match_id, team=team, competition_id=100, kickoff=kickoff, status=status, raw={}))


def add_appearance(db, match_id, player_id, name="Spelare"):
    db.add(Appearance(match_id=match_id, player_id=player_id, player_name=name))


# ---------------------------------------------------------------------------
# POST /api/overrides
# ---------------------------------------------------------------------------

class TestPostOverride:
    def test_unlock_ger_200(self, client, db):
        add_player(db, 42, "Kalle")
        db.flush()
        res = client.post("/api/overrides", json={"player_id": 42, "kind": "unlock", "note": "IBIS fel"})
        assert res.status_code == 200
        assert res.json()["ok"] is True

    def test_set_matches_left_ger_200(self, client, db):
        add_player(db, 42, "Kalle")
        db.flush()
        res = client.post("/api/overrides", json={"player_id": 42, "kind": "set_matches_left", "value": 1, "note": "test"})
        assert res.status_code == 200
        assert res.json()["ok"] is True

    def test_lock_ger_200(self, client, db):
        add_player(db, 42, "Kalle")
        db.flush()
        res = client.post("/api/overrides", json={"player_id": 42, "kind": "lock", "note": "Avstängd"})
        assert res.status_code == 200
        assert res.json()["ok"] is True

    def test_ogiltigt_kind_ger_400(self, client):
        res = client.post("/api/overrides", json={"player_id": 42, "kind": "ogiltig", "note": "x"})
        assert res.status_code == 400

    def test_ogiltigt_value_ger_400(self, client):
        res = client.post("/api/overrides", json={"player_id": 42, "kind": "set_matches_left", "value": 5, "note": "x"})
        assert res.status_code == 400

    def test_negativt_value_ger_400(self, client):
        res = client.post("/api/overrides", json={"player_id": 42, "kind": "set_matches_left", "value": -1, "note": "x"})
        assert res.status_code == 400

    def test_ersatter_befintlig_override(self, client, db):
        add_player(db, 42, "Kalle")
        db.flush()
        client.post("/api/overrides", json={"player_id": 42, "kind": "set_matches_left", "value": 0, "note": "first"})
        client.post("/api/overrides", json={"player_id": 42, "kind": "set_matches_left", "value": 2, "note": "second"})

        res = client.get("/api/status")
        player = next(p for p in res.json()["grupper"]["tillgangliga"] if p["player_id"] == 42)
        assert player["matcher_kvar"] == 2
        assert player["override"]["note"] == "second"


# ---------------------------------------------------------------------------
# DELETE /api/overrides/{player_id}
# ---------------------------------------------------------------------------

class TestDeleteOverride:
    def test_ta_bort_override_ger_200(self, client, db):
        add_player(db, 42, "Kalle")
        db.flush()
        client.post("/api/overrides", json={"player_id": 42, "kind": "set_matches_left", "value": 1, "note": "test"})
        res = client.delete("/api/overrides/42")
        assert res.status_code == 200
        assert res.json()["ok"] is True

    def test_ta_bort_obefintlig_override_ger_200(self, client):
        res = client.delete("/api/overrides/99999")
        assert res.status_code == 200

    def test_override_borta_efter_aterstall(self, client, db):
        add_player(db, 42, "Kalle")
        db.flush()
        client.post("/api/overrides", json={"player_id": 42, "kind": "set_matches_left", "value": 0, "note": "test"})
        client.delete("/api/overrides/42")

        res = client.get("/api/status")
        player = next((p for p in res.json()["grupper"]["tillgangliga"] if p["player_id"] == 42), None)
        assert player is not None
        assert player["override"] is None
        # Default matcher_kvar för ny spelare utan matcher
        assert player["matcher_kvar"] == 2


# ---------------------------------------------------------------------------
# Override syns i /api/status
# ---------------------------------------------------------------------------

class TestOverrideISynas:
    def test_unlock_appliceras_pa_last_spelare(self, client, db):
        add_match(db, 1, "A", datetime(2020, 1, 1))
        add_player(db, 42, "Kalle")
        add_appearance(db, 1, 42, "Kalle")
        db.flush()

        before = client.get("/api/status").json()
        assert any(p["player_id"] == 42 for p in before["grupper"]["lasta"])

        client.post("/api/overrides", json={"player_id": 42, "kind": "unlock", "note": "IBIS hade fel"})

        res = client.get("/api/status").json()
        assert not any(p["player_id"] == 42 for p in res["grupper"]["lasta"])
        alla = res["grupper"]["tillgangliga"] + res["grupper"]["maste_sta_over"]
        player = next(p for p in alla if p["player_id"] == 42)
        assert player["override"] is not None
        assert player["override"]["kind"] == "unlock"
        assert player["lock_orsak"] is None

    def test_set_matches_left_noll_hamnar_i_maste_sta_over(self, client, db):
        add_player(db, 42, "Kalle")
        db.flush()

        client.post("/api/overrides", json={"player_id": 42, "kind": "set_matches_left", "value": 0, "note": "test"})

        res = client.get("/api/status").json()
        assert any(p["player_id"] == 42 for p in res["grupper"]["maste_sta_over"])
        player = next(p for p in res["grupper"]["maste_sta_over"] if p["player_id"] == 42)
        assert player["matcher_kvar"] == 0
        assert player["override"]["value"] == 0

    def test_set_matches_left_tva_hamnar_i_tillgangliga(self, client, db):
        add_match(db, 1, "B", datetime(2020, 1, 1))
        add_match(db, 2, "A", datetime(2020, 1, 10))
        add_match(db, 3, "A", datetime(2020, 1, 20))
        add_player(db, 42, "Kalle")
        for mid in (1, 2, 3):
            add_appearance(db, mid, 42, "Kalle")
        db.flush()

        before = client.get("/api/status").json()
        assert any(p["player_id"] == 42 for p in before["grupper"]["maste_sta_over"])

        client.post("/api/overrides", json={"player_id": 42, "kind": "set_matches_left", "value": 2, "note": "test"})

        res = client.get("/api/status").json()
        assert any(p["player_id"] == 42 for p in res["grupper"]["tillgangliga"])

    def test_lock_appliceras_pa_tillganglig_spelare(self, client, db):
        add_match(db, 1, "B", datetime(2020, 1, 1))
        add_player(db, 42, "Kalle")
        add_appearance(db, 1, 42, "Kalle")
        db.flush()

        before = client.get("/api/status").json()
        assert any(p["player_id"] == 42 for p in before["grupper"]["tillgangliga"])

        client.post("/api/overrides", json={"player_id": 42, "kind": "lock", "note": "Avstängd i disciplinnämnd"})

        res = client.get("/api/status").json()
        lasta = res["grupper"]["lasta"]
        player = next(p for p in lasta if p["player_id"] == 42)
        assert player["lock_orsak"] is None
        assert player["override"]["kind"] == "lock"
        assert player["override"]["value"] is None
        assert not any(p["player_id"] == 42 for p in res["grupper"]["tillgangliga"])

    def test_lock_pa_spelare_utan_matcher(self, client, db):
        add_player(db, 42, "Kalle")
        db.flush()

        client.post("/api/overrides", json={"player_id": 42, "kind": "lock", "note": "test"})

        res = client.get("/api/status").json()
        assert any(p["player_id"] == 42 for p in res["grupper"]["lasta"])

    def test_lock_kan_aterstallas(self, client, db):
        add_match(db, 1, "B", datetime(2020, 1, 1))
        add_player(db, 42, "Kalle")
        add_appearance(db, 1, 42, "Kalle")
        db.flush()

        client.post("/api/overrides", json={"player_id": 42, "kind": "lock", "note": "test"})
        client.delete("/api/overrides/42")

        res = client.get("/api/status").json()
        assert not any(p["player_id"] == 42 for p in res["grupper"]["lasta"])
        assert any(p["player_id"] == 42 for p in res["grupper"]["tillgangliga"])

    def test_set_matches_left_lasar_upp_last_spelare(self, client, db):
        add_match(db, 1, "A", datetime(2020, 1, 1))
        add_player(db, 42, "Kalle")
        add_appearance(db, 1, 42, "Kalle")
        db.flush()

        client.post("/api/overrides", json={"player_id": 42, "kind": "set_matches_left", "value": 1, "note": "test"})

        res = client.get("/api/status").json()
        assert not any(p["player_id"] == 42 for p in res["grupper"]["lasta"])
        player = next(p for p in res["grupper"]["tillgangliga"] if p["player_id"] == 42)
        assert player["matcher_kvar"] == 1

    def test_override_inkluderar_note_och_created_at(self, client, db):
        add_player(db, 42, "Kalle")
        db.flush()
        client.post("/api/overrides", json={"player_id": 42, "kind": "set_matches_left", "value": 1, "note": "Anledning"})

        res = client.get("/api/status").json()
        player = next(p for p in res["grupper"]["tillgangliga"] if p["player_id"] == 42)
        ovr = player["override"]
        assert ovr["note"] == "Anledning"
        assert "created_at" in ovr
        assert ovr["kind"] == "set_matches_left"
        assert ovr["value"] == 1


# ---------------------------------------------------------------------------
# Stale-detektering
# ---------------------------------------------------------------------------

class TestStaleDetektering:
    def test_override_inte_stale_utan_nya_matcher(self, client, db):
        add_match(db, 1, "B", datetime(2020, 1, 1))
        add_player(db, 42, "Kalle")
        db.flush()

        client.post("/api/overrides", json={"player_id": 42, "kind": "set_matches_left", "value": 1, "note": "test"})

        res = client.get("/api/status").json()
        player = next(p for p in res["grupper"]["tillgangliga"] if p["player_id"] == 42)
        assert player["override"]["stale"] is False

    def test_override_stale_nar_ny_spelad_match_finns(self, client, db):
        add_match(db, 1, "B", datetime(2020, 1, 1))
        add_player(db, 42, "Kalle")
        db.flush()

        # Override sätts med data_snapshot = kickoff av match 1
        client.post("/api/overrides", json={"player_id": 42, "kind": "set_matches_left", "value": 1, "note": "test"})

        # Ny match tillkommer efter overriden
        add_match(db, 2, "A", datetime(2020, 6, 1))
        db.flush()

        res = client.get("/api/status").json()
        player = next(p for p in res["grupper"]["tillgangliga"] if p["player_id"] == 42)
        assert player["override"]["stale"] is True
