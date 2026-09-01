"""
Tester för FastAPI-endpoints (app/api.py).
Databasen är en in-memory SQLite-instans per test.
Synken är mockad – inga nätverksanrop.
"""

import threading
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api as api_mod
from app.api import app, get_db, _clear_status_cache, _clear_live_cache
from app.auth import require_session
from app.ibis_client import IBISClient, IBISLineups
from app.models import Appearance, Base, Match, Player, PlayerTeam, SyncLog
from app.sync import SyncResult, _now_naive


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _reset_sync_state():
    with api_mod._sync_state_lock:
        api_mod._sync_state["running"] = False


@pytest.fixture(autouse=True)
def clear_cache():
    _clear_status_cache()
    _clear_live_cache()
    _reset_sync_state()
    yield
    _clear_status_cache()
    _clear_live_cache()
    _reset_sync_state()


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
                  opponent="Motståndarna", counts_for_rules=True, competition_type=1,
                  home_team=None, away_team=None, final_result_ts=None):
    raw = {
        "HomeTeamID": home_team_id,
        "AwayTeamID": away_team_id,
        "MainVenue": venue,
        "GoalsHomeTeam": goals_home,
        "GoalsAwayTeam": goals_away,
        "CompetitionTypeID": competition_type,
        "FinalResultCreatedTS": final_result_ts,
    }
    if home_team is not None:
        raw["HomeTeam"] = home_team
    if away_team is not None:
        raw["AwayTeam"] = away_team
    db.add(Match(
        match_id=match_id, team=team, competition_id=100,
        kickoff=kickoff, status=status, round_name=round_name, opponent=opponent,
        counts_for_rules=counts_for_rules,
        raw=raw,
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

    def test_trupp_spelare_utan_spelad_match_tas_inte_med(self, db, api_client):
        # Bara registrerad i truppen, har inte stått i en spelad match. Visas
        # inte i spelbarhetslistan (SPEC 5).
        add_player(db, 99, "Ny Spelare", "3")
        db.flush()

        data = api_client.get("/api/status").json()

        assert data["grupper"]["tillgangliga"] == []
        assert data["rakningar"]["tillgangliga"] == 0

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

        # Trupp utan spelad match: spelare 40 – tas inte med (SPEC 5)
        add_player(db, 40, "NyTrupp", "4")

        db.flush()

        response = api_client.get("/api/status")
        data = response.json()

        assert data["rakningar"]["maste_sta_over"] == 1
        assert data["rakningar"]["tillgangliga"] == 1   # bara Tillgänglig, inte NyTrupp
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
        # Tre spelare som alla stått i truppen i en spelad B-match → alla i
        # tillgangliga. Lagfiltret styrs av player_teams, inte av var de spelat.
        add_match(db, 90, "B", datetime(2020, 1, 1))
        add_player(db, 1, "A-spelare")
        add_player(db, 2, "B-spelare")
        add_player(db, 3, "Pendlare")
        for pid in (1, 2, 3):
            add_appearance(db, 90, pid)
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
# POST /api/sync – startar synken i bakgrunden och svarar direkt (SPEC 3.5)
# ---------------------------------------------------------------------------

class TestPostSync:
    def test_startar_synk_i_bakgrunden_och_svarar_direkt(self, api_client, monkeypatch):
        ran = threading.Event()

        def fake_worker():
            try:
                ran.set()
            finally:
                _reset_sync_state()

        monkeypatch.setattr("app.api._sync_worker", fake_worker)

        response = api_client.post("/api/sync")

        assert response.status_code == 200
        data = response.json()
        assert data["startad"] is True
        assert data["pagar"] is True
        assert ran.wait(2)

    def test_andra_synk_medan_en_pagar_startar_inte_en_till(self, api_client, monkeypatch):
        started = threading.Event()
        release = threading.Event()
        calls = []

        def fake_worker():
            calls.append(1)
            started.set()
            release.wait(3)
            _reset_sync_state()

        monkeypatch.setattr("app.api._sync_worker", fake_worker)

        first = api_client.post("/api/sync")
        assert started.wait(2)
        second = api_client.post("/api/sync")

        assert first.json()["startad"] is True
        assert second.json()["startad"] is False
        assert second.json()["pagar"] is True

        release.set()
        for _ in range(100):
            with api_mod._sync_state_lock:
                if not api_mod._sync_state["running"]:
                    break
            time.sleep(0.02)
        assert calls == [1]

    def test_cachen_byggs_om_nar_bakgrundssynken_blir_klar(self, db, api_client, monkeypatch):
        before = api_client.get("/api/status").json()
        assert before["rakningar"]["tillgangliga"] == 0

        add_match(db, 1, "B", datetime(2020, 1, 1))
        add_player(db, 99, "Ny", "1")
        add_appearance(db, 1, 99, "Ny")
        db.flush()

        done = threading.Event()

        def fake_worker():
            try:
                new_status = api_mod._build_status_response(db)
                with api_mod._cache_lock:
                    api_mod._status_cache = new_status
            finally:
                _reset_sync_state()
                done.set()

        monkeypatch.setattr("app.api._sync_worker", fake_worker)

        api_client.post("/api/sync")
        assert done.wait(2)

        after = api_client.get("/api/status").json()
        assert after["rakningar"]["tillgangliga"] == 1


# ---------------------------------------------------------------------------
# _sync_worker – bakgrundstasken får inte svälja fel (SPEC 3.5)
#
# Ett misslyckande ska synas både i Railway-loggen och i sync_log, inte bara i
# konsolen när synken körs manuellt.
# ---------------------------------------------------------------------------

class TestSyncWorkerFelhantering:
    def _mem_sessionmaker(self):
        eng = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(eng)
        return sessionmaker(bind=eng, autoflush=True)

    def test_ovantat_fel_skrivs_till_railway_och_sync_log(self, monkeypatch, caplog):
        make_session = self._mem_sessionmaker()
        monkeypatch.setattr(api_mod, "SessionLocal", make_session)
        monkeypatch.setattr(api_mod, "IBISClient", lambda *a, **kw: MagicMock())

        def boom(db, client):
            raise RuntimeError("iBIS-token gick inte att hämta")

        monkeypatch.setattr(api_mod, "run_sync", boom)

        with api_mod._sync_state_lock:
            api_mod._sync_state["running"] = True

        with caplog.at_level("ERROR", logger="app.api"):
            api_mod._sync_worker()

        # running-flaggan är alltid återställd efteråt
        with api_mod._sync_state_lock:
            assert api_mod._sync_state["running"] is False

        # Felet syns i Railway-loggen ...
        assert any(
            "avbröts med ett oväntat fel" in r.getMessage() for r in caplog.records
        )

        # ... och i sync_log
        with make_session() as s:
            rows = list(s.scalars(select(SyncLog)))
            assert len(rows) == 1
            assert rows[0].ok is False
            assert rows[0].finished_at is not None

    def test_misslyckad_synk_loggas_till_railway(self, monkeypatch, caplog):
        make_session = self._mem_sessionmaker()
        monkeypatch.setattr(api_mod, "SessionLocal", make_session)
        monkeypatch.setattr(api_mod, "IBISClient", lambda *a, **kw: MagicMock())

        now = _now_naive()

        def fake_run_sync(db, client):
            return SyncResult(
                ok=False,
                matches_added=0,
                warnings=["Synken avbröts med fel: nätverksfel"],
                started_at=now,
                finished_at=now,
                log_id=1,
            )

        monkeypatch.setattr(api_mod, "run_sync", fake_run_sync)

        with api_mod._sync_state_lock:
            api_mod._sync_state["running"] = True

        with caplog.at_level("WARNING", logger="app.api"):
            api_mod._sync_worker()

        messages = [r.getMessage() for r in caplog.records]
        assert any("Bakgrundssynk misslyckades" in m for m in messages)
        assert any("nätverksfel" in m for m in messages)
        with api_mod._sync_state_lock:
            assert api_mod._sync_state["running"] is False


# ---------------------------------------------------------------------------
# GET /api/sync/status – pågår-flagga plus sammanfattning av senaste körningen
# ---------------------------------------------------------------------------

class TestGetSyncStatus:
    def test_ingen_synk_och_ingen_logg(self, api_client):
        response = api_client.get("/api/sync/status")

        assert response.status_code == 200
        data = response.json()
        assert data["pagar"] is False
        assert data["senaste"] is None

    def test_sammanfattar_senaste_synkloggen(self, db, api_client):
        db.add(SyncLog(
            started_at=datetime(2026, 8, 27, 3, 0),
            finished_at=datetime(2026, 8, 27, 3, 2),
            matches_added=4,
            warnings=["Match 9 avbruten utan resultat"],
            ok=True,
        ))
        db.flush()

        data = api_client.get("/api/sync/status").json()

        assert data["pagar"] is False
        assert data["senaste"]["ok"] is True
        assert data["senaste"]["matcher_tillagda"] == 4
        assert data["senaste"]["klar"] is not None
        assert "Match 9 avbruten utan resultat" in data["senaste"]["varningar"]

    def test_pagar_flagga_speglar_sync_state(self, api_client):
        with api_mod._sync_state_lock:
            api_mod._sync_state["running"] = True
        try:
            data = api_client.get("/api/sync/status").json()
            assert data["pagar"] is True
        finally:
            _reset_sync_state()


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

    def test_lagmal_till_matchhuvudet_bortalag(self, db, api_client):
        # Lag B är borta (home_team_id != team_b_id). Våra mål = borta-målen.
        add_match_raw(db, 1, "B", datetime(2026, 9, 1, 13), status="played",
                      home_team_id=9999, away_team_id=17541,
                      goals_home=2, goals_away=5,
                      home_team="Värdlaget IBK", away_team="Tungelsta IF (B)")
        db.flush()

        data = api_client.get("/api/matches/1").json()
        assert data["hemma"] is False
        assert data["mal"] == 5
        assert data["motstandare_mal"] == 2
        assert data["hemmalag"] == "Värdlaget IBK"
        assert data["bortalag"] == "Tungelsta IF (B)"

    def test_lagmal_saknas_tills_matchen_spelats(self, db, api_client):
        add_match_raw(db, 1, "B", datetime(2026, 9, 1, 13), status="scheduled",
                      home_team_id=17541, away_team_id=9999)
        db.flush()

        data = api_client.get("/api/matches/1").json()
        assert data["mal"] is None
        assert data["motstandare_mal"] is None


# ---------------------------------------------------------------------------
# Skottsynk – GET/POST /api/matches/{id}/shot-events  (steg 14)
# ---------------------------------------------------------------------------

def shot_event(id, *, player_id=10, side="egen", kind="on_goal", period=1,
               created_at="2026-09-01T18:05:00.000Z", created_by="Theo",
               deleted_at=None):
    return {
        "id": id, "player_id": player_id, "side": side, "kind": kind,
        "period": period, "created_at": created_at, "created_by": created_by,
        "deleted_at": deleted_at,
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

    def test_egna_skott_defaultar_till_side_egen(self, db, api_client):
        self._match(db)
        api_client.post("/api/matches/1/shot-events", json={"handelser": [
            shot_event("55555555-5555-4555-8555-555555555555"),
        ]})
        h = api_client.get("/api/matches/1/shot-events").json()["handelser"][0]
        assert h["side"] == "egen"
        assert h["player_id"] == 10

    def test_motstandarens_skott_sparas_utan_spelare(self, db, api_client):
        self._match(db)
        res = api_client.post("/api/matches/1/shot-events", json={"handelser": [
            shot_event("66666666-6666-4666-8666-666666666666",
                       side="motstandare", player_id=None, kind="blocked"),
        ]})
        assert res.status_code == 200

        h = api_client.get("/api/matches/1/shot-events").json()["handelser"][0]
        assert h["side"] == "motstandare"
        assert h["player_id"] is None
        assert h["kind"] == "blocked"

    def test_motstandarskott_med_spelare_ignorerar_spelaren(self, db, api_client):
        self._match(db)
        api_client.post("/api/matches/1/shot-events", json={"handelser": [
            shot_event("77777777-7777-4777-8777-777777777777",
                       side="motstandare", player_id=10),
        ]})
        h = api_client.get("/api/matches/1/shot-events").json()["handelser"][0]
        assert h["player_id"] is None

    def test_eget_skott_utan_spelare_ger_422(self, db, api_client):
        self._match(db)
        res = api_client.post("/api/matches/1/shot-events", json={"handelser": [
            shot_event("x", side="egen", player_id=None),
        ]})
        assert res.status_code == 422

    def test_ogiltig_side_ger_422(self, db, api_client):
        self._match(db)
        res = api_client.post("/api/matches/1/shot-events", json={"handelser": [
            shot_event("x", side="hemma"),
        ]})
        assert res.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/live – live-uppdatering av matchresultatet (SPEC 6.6)
# ---------------------------------------------------------------------------

def _ibis_match_dict(match_id, *, home_team_id=1977, away_team_id=9999,
                     kickoff_offset_hours=-1.0, goals_home=None, goals_away=None,
                     final_result_ts=None, competition_type=1):
    md = _now_naive() + timedelta(hours=kickoff_offset_hours)
    return {
        "MatchID": match_id,
        "CompetitionID": 100,
        "CompetitionTypeID": competition_type,
        "HomeTeamID": home_team_id,
        "HomeTeam": f"Hemmalag {home_team_id}",
        "AwayTeamID": away_team_id,
        "AwayTeam": f"Bortalag {away_team_id}",
        "MatchDateTime": md.replace(microsecond=0).isoformat(),
        "Cancelled": False,
        "Postponed": False,
        "Abandoned": False,
        "GoalsHomeTeam": goals_home,
        "GoalsAwayTeam": goals_away,
        "FinalResultCreatedTS": final_result_ts,
        "Round": 1,
        "RoundName": "Omgång 1",
        "MatchStatus": None,
    }


def _team_raw(*match_dicts):
    return {
        "TeamID": 1977,
        "Name": "Testlag",
        "Competitions": [
            {"CompetitionID": 100, "CompetitionTypeID": 1, "Name": "Serien",
             "Matches": list(match_dicts)},
        ],
        "Players": [],
    }


def _lineups(match_id, away_players, *, home_id=1977, away_id=17541):
    return IBISLineups.model_validate({
        "MatchID": match_id,
        "HomeTeamID": home_id,
        "AwayTeamID": away_id,
        "HomeTeamPlayers": [],
        "AwayTeamPlayers": away_players,
        "HomeTeamTeamPersons": [],
        "AwayTeamTeamPersons": [],
    })


def _fake_client(team_raw, lineups_by_id=None):
    client = MagicMock(spec=IBISClient)
    client.fetch_team_raw.return_value = team_raw
    client.fetch_lineups.side_effect = lambda mid: (lineups_by_id or {})[mid]
    return client


class TestGetLive:
    def test_ingen_pagaende_match_ger_tomt_utan_ibis(self, db, api_client, monkeypatch):
        # En match långt fram i tiden – inte pågående.
        add_match_raw(db, 1, "B", _now_naive() + timedelta(days=3))
        db.flush()

        def boom(*a, **kw):
            raise AssertionError("iBIS ska inte anropas när ingen match pågår")

        monkeypatch.setattr("app.api.IBISClient", boom)

        res = api_client.get("/api/live")
        assert res.status_code == 200
        assert res.json()["matcher"] == []

    def test_fardigrapporterad_match_raknas_inte_som_pagaende(self, db, api_client, monkeypatch):
        add_match_raw(db, 1, "B", _now_naive() - timedelta(hours=1),
                      status="played", final_result_ts="2026-09-01T15:00:00")
        db.flush()
        monkeypatch.setattr("app.api.IBISClient",
                            lambda *a, **kw: (_ for _ in ()).throw(AssertionError()))

        assert api_client.get("/api/live").json()["matcher"] == []

    def test_gammal_match_utanfor_fyratimmarsfonstret_ignoreras(self, db, api_client, monkeypatch):
        add_match_raw(db, 1, "B", _now_naive() - timedelta(hours=5))
        db.flush()
        monkeypatch.setattr("app.api.IBISClient",
                            lambda *a, **kw: (_ for _ in ()).throw(AssertionError()))

        assert api_client.get("/api/live").json()["matcher"] == []

    def test_pagaende_match_ger_resultat_och_spelarstatistik(self, db, api_client, monkeypatch):
        # Lag B spelar borta (AwayTeamID == 17541), hemmalaget leder 2-1.
        add_match_raw(db, 500, "B", _now_naive() - timedelta(hours=1),
                      home_team_id=9999, away_team_id=17541)
        db.flush()

        team_raw = _team_raw(_ibis_match_dict(
            500, home_team_id=9999, away_team_id=17541,
            goals_home=2, goals_away=1,
        ))
        lineups = {500: _lineups(500, [
            {"MatchPlayerID": 1, "PlayerID": 10, "Name": "Spelare Tio",
             "Goals": 1, "Assists": 0, "PenaltyMinutes": 2},
        ], home_id=9999, away_id=17541)}
        monkeypatch.setattr("app.api.IBISClient", lambda *a, **kw: _fake_client(team_raw, lineups))

        data = api_client.get("/api/live").json()
        assert len(data["matcher"]) == 1
        row = data["matcher"][0]
        assert row["match_id"] == 500
        assert row["hemma"] is False
        assert row["mal"] == 1            # våra (borta-)mål
        assert row["motstandare_mal"] == 2
        assert row["resultat"] == {"hemma": 2, "borta": 1}
        assert row["status"] == "played"
        assert row["spelare"] == [
            {"player_id": 10, "mal": 1, "assist": 0, "utvisningsminuter": 2},
        ]

    def test_svaret_cachas_sa_flera_pollningar_ger_ett_ibis_anrop(self, db, api_client, monkeypatch):
        # Lag B spelar borta, leder 3-0.
        add_match_raw(db, 1, "B", _now_naive() - timedelta(hours=1),
                      home_team_id=9999, away_team_id=17541)
        db.flush()

        team_raw = _team_raw(_ibis_match_dict(
            1, home_team_id=9999, away_team_id=17541, goals_home=0, goals_away=3,
        ))
        lineups = {1: _lineups(1, [], home_id=9999, away_id=17541)}
        calls = {"n": 0}

        def factory(*a, **kw):
            calls["n"] += 1
            return _fake_client(team_raw, lineups)

        monkeypatch.setattr("app.api.IBISClient", factory)

        first = api_client.get("/api/live").json()
        second = api_client.get("/api/live").json()
        assert first == second
        assert first["matcher"][0]["mal"] == 3
        assert calls["n"] == 1

    def test_ibis_nere_utan_cache_ger_felkod(self, db, api_client, monkeypatch):
        add_match_raw(db, 1, "B", _now_naive() - timedelta(hours=1))
        db.flush()

        def factory(*a, **kw):
            c = MagicMock(spec=IBISClient)
            c.fetch_team_raw.side_effect = ConnectionError("iBIS nere")
            return c

        monkeypatch.setattr("app.api.IBISClient", factory)

        res = api_client.get("/api/live")
        assert res.status_code == 502
