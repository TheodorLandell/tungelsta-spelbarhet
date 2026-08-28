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
from app.models import Appearance, Base, Match, Player, PlayerTeam, SyncLog
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


def add_match_raw(db, match_id, team, kickoff, status="scheduled", *,
                  home_team_id=1977, away_team_id=9999, venue="Tungelstahallen",
                  goals_home=None, goals_away=None, round_name="Omgång 1",
                  opponent="Motståndarna", counts_for_rules=True, competition_type=1):
    db.add(Match(
        match_id=match_id, team=team, competition_id=100,
        kickoff=kickoff, status=status, round_name=round_name, opponent=opponent,
        counts_for_rules=counts_for_rules,
        raw={
            "HomeTeamID": home_team_id,
            "AwayTeamID": away_team_id,
            "MainVenue": venue,
            "GoalsHomeTeam": goals_home,
            "GoalsAwayTeam": goals_away,
            "CompetitionTypeID": competition_type,
        },
    ))


def add_appearance(db, match_id, player_id, name="Testspelare", *,
                   shirt_no=None, goals=0, assists=0, penalty_minutes=0):
    db.add(Appearance(
        match_id=match_id, player_id=player_id, player_name=name,
        shirt_no=shirt_no, goals=goals, assists=assists,
        penalty_minutes=penalty_minutes,
    ))


def add_player(db, player_id, name="Spelare", shirt_no="9", is_goalkeeper=False):
    db.add(Player(
        player_id=player_id, name=name, shirt_no=shirt_no,
        is_goalkeeper=is_goalkeeper, last_seen=datetime(2026, 1, 1),
    ))


def add_player_team(db, player_id, team):
    db.add(PlayerTeam(player_id=player_id, team=team))


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
# GET /api/status?team= – lagfilter
# ---------------------------------------------------------------------------

class TestTeamFilter:
    def _seed(self, db):
        # Tre truppspelare utan matcher → alla i tillgangliga
        add_player(db, 1, "A-spelare")
        add_player(db, 2, "B-spelare")
        add_player(db, 3, "Pendlare")
        add_player_team(db, 1, "A")
        add_player_team(db, 2, "B")
        add_player_team(db, 3, "A")
        add_player_team(db, 3, "B")
        db.flush()

    def test_utan_param_visar_alla(self, db, api_client):
        self._seed(db)
        data = api_client.get("/api/status").json()
        ids = {p["player_id"] for p in data["grupper"]["tillgangliga"]}
        assert ids == {1, 2, 3}
        assert data["rakningar"]["tillgangliga"] == 3

    def test_team_a_filtrerar_bort_rena_b_spelare(self, db, api_client):
        self._seed(db)
        data = api_client.get("/api/status?team=A").json()
        ids = {p["player_id"] for p in data["grupper"]["tillgangliga"]}
        assert ids == {1, 3}
        assert data["rakningar"]["tillgangliga"] == 2

    def test_team_b_filtrerar_bort_rena_a_spelare(self, db, api_client):
        self._seed(db)
        data = api_client.get("/api/status?team=B").json()
        ids = {p["player_id"] for p in data["grupper"]["tillgangliga"]}
        assert ids == {2, 3}

    def test_spelare_i_bada_lagen_visas_for_bada(self, db, api_client):
        self._seed(db)
        a = api_client.get("/api/status?team=A").json()
        b = api_client.get("/api/status?team=B").json()
        assert any(p["player_id"] == 3 for p in a["grupper"]["tillgangliga"])
        assert any(p["player_id"] == 3 for p in b["grupper"]["tillgangliga"])

    def test_ogiltig_team_ger_400(self, db, api_client):
        self._seed(db)
        res = api_client.get("/api/status?team=C")
        assert res.status_code == 400

    def test_row_innehaller_lag(self, db, api_client):
        self._seed(db)
        data = api_client.get("/api/status").json()
        row = next(p for p in data["grupper"]["tillgangliga"] if p["player_id"] == 3)
        assert row["lag"] == ["A", "B"]


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


# ---------------------------------------------------------------------------
# GET /api/matches – matchlista per lag
# ---------------------------------------------------------------------------

class TestGetMatches:
    def test_kraver_lag_och_returnerar_i_datumordning(self, db, api_client):
        add_match_raw(db, 3, "B", datetime(2026, 10, 5, 15), opponent="Sist")
        add_match_raw(db, 1, "B", datetime(2026, 9, 1, 13), opponent="Först")
        add_match_raw(db, 2, "B", datetime(2026, 9, 20, 14), opponent="Mitten")
        add_match_raw(db, 9, "A", datetime(2026, 9, 2, 13), opponent="A-match")
        db.flush()

        data = api_client.get("/api/matches?team=B").json()

        assert [m["match_id"] for m in data["matcher"]] == [1, 2, 3]
        assert data["matcher"][0]["motstandare"] == "Först"

    def test_utan_lag_ger_422(self, db, api_client):
        assert api_client.get("/api/matches").status_code == 422

    def test_ogiltigt_lag_ger_400(self, db, api_client):
        assert api_client.get("/api/matches?team=C").status_code == 400

    def test_hemma_borta_och_hall(self, db, api_client):
        add_match_raw(db, 1, "B", datetime(2026, 9, 1, 13),
                      home_team_id=17541, away_team_id=9999, venue="Brandbergshallen")
        add_match_raw(db, 2, "B", datetime(2026, 9, 8, 13),
                      home_team_id=9999, away_team_id=17541, venue="Bortahallen")
        db.flush()

        matcher = {m["match_id"]: m for m in api_client.get("/api/matches?team=B").json()["matcher"]}

        assert matcher[1]["hemma"] is True
        assert matcher[1]["hall"] == "Brandbergshallen"
        assert matcher[2]["hemma"] is False

    def test_resultat_visas_bara_for_spelad_match(self, db, api_client):
        add_match_raw(db, 1, "B", datetime(2026, 9, 1, 13), status="played",
                      goals_home=5, goals_away=3)
        add_match_raw(db, 2, "B", datetime(2026, 9, 8, 13), status="scheduled",
                      goals_home=None, goals_away=None)
        db.flush()

        matcher = {m["match_id"]: m for m in api_client.get("/api/matches?team=B").json()["matcher"]}

        assert matcher[1]["resultat"] == {"hemma": 5, "borta": 3}
        assert matcher[2]["resultat"] is None

    def test_installd_match_har_status_cancelled(self, db, api_client):
        add_match_raw(db, 1, "B", datetime(2026, 9, 1, 13), status="cancelled")
        db.flush()

        data = api_client.get("/api/matches?team=B").json()
        assert data["matcher"][0]["status"] == "cancelled"

    def test_tom_raw_kraschar_inte(self, db, api_client):
        add_match(db, 1, "B", datetime(2026, 9, 1, 13), status="scheduled")
        db.flush()

        data = api_client.get("/api/matches?team=B").json()
        row = data["matcher"][0]
        assert row["hemma"] is None
        assert row["hall"] is None
        assert row["resultat"] is None

    def test_seriematch_markeras_som_raknande(self, db, api_client):
        add_match_raw(db, 1, "A", datetime(2026, 9, 1, 13))
        db.flush()

        row = api_client.get("/api/matches?team=A").json()["matcher"][0]
        assert row["raknas"] is True
        assert row["matchtyp"] == "serie"

    def test_cupmatch_markeras_och_raknas_inte(self, db, api_client):
        add_match_raw(db, 1, "A", datetime(2026, 9, 1, 13),
                      counts_for_rules=False, competition_type=3)
        db.flush()

        row = api_client.get("/api/matches?team=A").json()["matcher"][0]
        assert row["raknas"] is False
        assert row["matchtyp"] == "cup"

    def test_traningsmatch_markeras_och_raknas_inte(self, db, api_client):
        add_match_raw(db, 1767137, "A", datetime(2026, 9, 1, 20, 20),
                      counts_for_rules=False, competition_type=5,
                      opponent="Hammarby IF IBF Herr A")
        db.flush()

        row = api_client.get("/api/matches?team=A").json()["matcher"][0]
        assert row["raknas"] is False
        assert row["matchtyp"] == "traningsmatch"


# ---------------------------------------------------------------------------
# GET /api/matches/{id} – matchvy med trupp
# ---------------------------------------------------------------------------

class TestGetMatch:
    def test_okand_match_ger_404(self, db, api_client):
        assert api_client.get("/api/matches/999").status_code == 404

    def test_trupp_ej_publicerad_nar_appearances_saknas(self, db, api_client):
        add_match_raw(db, 1, "B", datetime(2026, 9, 1, 13), status="scheduled")
        db.flush()

        data = api_client.get("/api/matches/1").json()
        assert data["trupp_publicerad"] is False
        assert data["trupp"] == []
        assert data["spelad"] is False

    def test_trupp_fran_appearances_med_malvakt_markerad(self, db, api_client):
        add_match_raw(db, 1, "B", datetime(2026, 9, 1, 13), status="scheduled")
        add_player(db, 10, "Utespelare", "7")
        add_player(db, 11, "Målvakten", "1", is_goalkeeper=True)
        add_appearance(db, 1, 10, "Utespelare", shirt_no="7")
        add_appearance(db, 1, 11, "Målvakten", shirt_no="1")
        db.flush()

        data = api_client.get("/api/matches/1").json()

        assert data["trupp_publicerad"] is True
        # Målvakt först, sedan tröjnummerordning
        assert [p["player_id"] for p in data["trupp"]] == [11, 10]
        assert data["trupp"][0]["malvakt"] is True
        assert data["trupp"][1]["malvakt"] is False

    def test_statistik_bara_nar_matchen_ar_spelad(self, db, api_client):
        add_match_raw(db, 1, "B", datetime(2026, 9, 1, 13), status="scheduled")
        add_appearance(db, 1, 10, "Spelare", shirt_no="7", goals=2, assists=1,
                       penalty_minutes=2)
        db.flush()

        row = api_client.get("/api/matches/1").json()["trupp"][0]
        assert row["mal"] is None
        assert row["assist"] is None
        assert row["utvisningsminuter"] is None

    def test_statistik_visas_for_spelad_match(self, db, api_client):
        add_match_raw(db, 1, "B", datetime(2026, 9, 1, 13), status="played",
                      goals_home=3, goals_away=1)
        add_appearance(db, 1, 10, "Spelare", shirt_no="7", goals=2, assists=1,
                       penalty_minutes=2)
        db.flush()

        data = api_client.get("/api/matches/1").json()
        assert data["spelad"] is True
        row = data["trupp"][0]
        assert row["mal"] == 2
        assert row["assist"] == 1
        assert row["utvisningsminuter"] == 2


# ---------------------------------------------------------------------------
# Skottsynk – GET/POST /api/matches/{id}/shot-events  (steg 14)
# ---------------------------------------------------------------------------

def shot_event(id, *, player_id=10, kind="on_goal", period=1,
               created_at="2026-09-01T18:05:00.000Z", created_by="Theo",
               deleted_at=None):
    return {
        "id": id, "player_id": player_id, "kind": kind, "period": period,
        "created_at": created_at, "created_by": created_by, "deleted_at": deleted_at,
    }


class TestShotEvents:
    def _match(self, db):
        add_match_raw(db, 1, "B", datetime(2026, 9, 1, 13), status="scheduled")
        add_player(db, 10, "Spelare", "7")
        db.flush()

    def test_okand_match_ger_404(self, db, api_client):
        assert api_client.get("/api/matches/999/shot-events").status_code == 404
        res = api_client.post("/api/matches/999/shot-events",
                              json={"handelser": [shot_event("a")]})
        assert res.status_code == 404

    def test_batch_sparas_och_hamtas(self, db, api_client):
        self._match(db)
        res = api_client.post("/api/matches/1/shot-events", json={"handelser": [
            shot_event("11111111-1111-4111-8111-111111111111", kind="on_goal"),
            shot_event("22222222-2222-4222-8222-222222222222", kind="missed", period=2),
        ]})
        assert res.status_code == 200
        assert res.json()["antal"] == 2

        data = api_client.get("/api/matches/1/shot-events").json()
        assert [h["id"] for h in data["handelser"]] == [
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
        ]
        assert data["handelser"][0]["kind"] == "on_goal"
        assert data["handelser"][1]["period"] == 2
        assert data["handelser"][0]["created_by"] == "Theo"

    def test_samma_batch_tva_ganger_ger_inga_dubbletter(self, db, api_client):
        self._match(db)
        batch = {"handelser": [shot_event("33333333-3333-4333-8333-333333333333")]}
        api_client.post("/api/matches/1/shot-events", json=batch)
        api_client.post("/api/matches/1/shot-events", json=batch)

        data = api_client.get("/api/matches/1/shot-events").json()
        assert len(data["handelser"]) == 1

    def test_tombstone_synkas_som_vanlig_handelse(self, db, api_client):
        self._match(db)
        eid = "44444444-4444-4444-8444-444444444444"
        # Först den aktiva händelsen
        api_client.post("/api/matches/1/shot-events",
                        json={"handelser": [shot_event(eid)]})
        # Sedan samma id igen, nu med deleted_at satt
        api_client.post("/api/matches/1/shot-events", json={"handelser": [
            shot_event(eid, deleted_at="2026-09-01T18:30:00.000Z"),
        ]})

        data = api_client.get("/api/matches/1/shot-events").json()
        assert len(data["handelser"]) == 1
        assert data["handelser"][0]["deleted_at"] is not None

    def test_ogiltig_kategori_ger_422(self, db, api_client):
        self._match(db)
        res = api_client.post("/api/matches/1/shot-events",
                              json={"handelser": [shot_event("x", kind="goal")]})
        assert res.status_code == 422

    def test_ogiltig_period_ger_422(self, db, api_client):
        self._match(db)
        res = api_client.post("/api/matches/1/shot-events",
                              json={"handelser": [shot_event("x", period=4)]})
        assert res.status_code == 422

    def test_tom_batch_ar_ok(self, db, api_client):
        self._match(db)
        res = api_client.post("/api/matches/1/shot-events", json={"handelser": []})
        assert res.status_code == 200
        assert res.json()["antal"] == 0
