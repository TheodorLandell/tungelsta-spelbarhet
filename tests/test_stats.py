"""
Tester för steg 17 – statistiksidan (SPEC 7).

Per spelare, för valt lag och vald omfattning: matcher i truppen, mål, assist,
poäng, utvisningsminuter från iBIS, samt skott totalt och de fyra andelarna som
summerar till 100. Bara spelade seriematcher räknas. Skottdata finns bara för
matcher där någon registrerat – saknas den visas inget, aldrig noll.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import app, get_db, _clear_status_cache
from app.auth import require_session
from app.models import (
    Appearance,
    Base,
    Match,
    Player,
    PlayerTeam,
    RosterEdit,
    ShotEvent,
)


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


# ---------------------------------------------------------------------------
# Hjälpfunktioner
# ---------------------------------------------------------------------------

def add_match(db, match_id, team, kickoff, status="played", counts_for_rules=True):
    db.add(Match(
        match_id=match_id, team=team, competition_id=100, kickoff=kickoff,
        status=status, counts_for_rules=counts_for_rules, raw={},
    ))


def add_player(db, player_id, name="Spelare", shirt_no="9", is_goalkeeper=False):
    db.add(Player(
        player_id=player_id, name=name, shirt_no=shirt_no,
        is_goalkeeper=is_goalkeeper, last_seen=datetime(2026, 1, 1),
    ))


def add_appearance(db, match_id, player_id, name="Spelare", *,
                   shirt_no=None, goals=0, assists=0, penalty_minutes=0):
    db.add(Appearance(
        match_id=match_id, player_id=player_id, player_name=name,
        shirt_no=shirt_no, goals=goals, assists=assists,
        penalty_minutes=penalty_minutes,
    ))


def add_shot(db, shot_id, match_id, player_id, kind, *, side="egen",
             period=1, deleted_at=None):
    db.add(ShotEvent(
        id=shot_id, match_id=match_id, player_id=player_id, side=side, kind=kind,
        period=period, created_at=datetime(2026, 9, 1, 19, 0),
        created_by="Theo", deleted_at=deleted_at,
    ))


def add_roster_edit(db, match_id, player_id, action, note="iBIS-fel"):
    db.add(RosterEdit(
        match_id=match_id, player_id=player_id, action=action, note=note,
        created_at=datetime(2026, 8, 28, 12, 0), created_by="Theo",
    ))


def rows_by_id(data):
    return {r["player_id"]: r for r in data["spelare"]}


# ---------------------------------------------------------------------------
# Grundfall och validering
# ---------------------------------------------------------------------------

class TestValidering:
    def test_tom_db_ger_tom_lista(self, client):
        data = client.get("/api/stats?team=A").json()
        assert data["spelare"] == []
        assert data["omfattning"]["antal_matcher"] == 0

    def test_ogiltigt_lag_ger_400(self, client):
        assert client.get("/api/stats?team=C").status_code == 400

    def test_utan_lag_ger_422(self, client):
        assert client.get("/api/stats").status_code == 422

    def test_ogiltig_omfattning_ger_400(self, client):
        assert client.get("/api/stats?team=A&scope=allt").status_code == 400

    def test_n_under_ett_ger_400(self, client):
        assert client.get("/api/stats?team=A&scope=senaste_n&n=0").status_code == 400


# ---------------------------------------------------------------------------
# iBIS-aggregat
# ---------------------------------------------------------------------------

class TestIbisAggregat:
    def test_summerar_mal_assist_poang_utvisning_over_sasongen(self, client, db):
        add_match(db, 1, "A", datetime(2026, 9, 1))
        add_match(db, 2, "A", datetime(2026, 9, 8))
        add_player(db, 10, "Kalle", "7")
        add_appearance(db, 1, 10, "Kalle", goals=2, assists=1, penalty_minutes=2)
        add_appearance(db, 2, 10, "Kalle", goals=1, assists=3, penalty_minutes=0)
        db.flush()

        row = rows_by_id(client.get("/api/stats?team=A").json())[10]
        assert row["matcher"] == 2
        assert row["mal"] == 3
        assert row["assist"] == 4
        assert row["poang"] == 7
        assert row["utvisningsminuter"] == 2

    def test_bara_spelade_matcher_raknas(self, client, db):
        add_match(db, 1, "A", datetime(2026, 9, 1), status="played")
        add_match(db, 2, "A", datetime(2026, 9, 8), status="scheduled")
        add_player(db, 10, "Kalle", "7")
        add_appearance(db, 1, 10, "Kalle", goals=1)
        add_appearance(db, 2, 10, "Kalle", goals=5)  # ospelad – ska ignoreras
        db.flush()

        row = rows_by_id(client.get("/api/stats?team=A").json())[10]
        assert row["matcher"] == 1
        assert row["mal"] == 1

    def test_cup_och_traningsmatch_raknas_inte(self, client, db):
        add_match(db, 1, "A", datetime(2026, 9, 1), counts_for_rules=True)
        add_match(db, 2, "A", datetime(2026, 9, 8), counts_for_rules=False)
        add_player(db, 10, "Kalle", "7")
        add_appearance(db, 1, 10, "Kalle", goals=1)
        add_appearance(db, 2, 10, "Kalle", goals=4)
        db.flush()

        data = client.get("/api/stats?team=A").json()
        assert data["omfattning"]["antal_matcher"] == 1
        assert rows_by_id(data)[10]["mal"] == 1

    def test_sorteras_efter_poang_fallande(self, client, db):
        add_match(db, 1, "A", datetime(2026, 9, 1))
        add_player(db, 10, "Fågel", "7")
        add_player(db, 20, "Anka", "8")
        add_appearance(db, 1, 10, "Fågel", goals=1, assists=0)
        add_appearance(db, 1, 20, "Anka", goals=2, assists=2)
        db.flush()

        ids = [r["player_id"] for r in client.get("/api/stats?team=A").json()["spelare"]]
        assert ids == [20, 10]


# ---------------------------------------------------------------------------
# Omfattning
# ---------------------------------------------------------------------------

class TestOmfattning:
    def _seed(self, db):
        for i, day in enumerate((1, 8, 15, 22), start=1):
            add_match(db, i, "A", datetime(2026, 9, day))
        add_player(db, 10, "Kalle", "7")
        for mid in (1, 2, 3, 4):
            add_appearance(db, mid, 10, "Kalle", goals=1)
        db.flush()

    def test_senaste_matchen(self, client, db):
        self._seed(db)
        data = client.get("/api/stats?team=A&scope=senaste").json()
        assert data["omfattning"]["antal_matcher"] == 1
        assert rows_by_id(data)[10]["mal"] == 1

    def test_senaste_n(self, client, db):
        self._seed(db)
        data = client.get("/api/stats?team=A&scope=senaste_n&n=2").json()
        assert data["omfattning"]["antal_matcher"] == 2
        assert rows_by_id(data)[10]["mal"] == 2

    def test_n_storre_an_antal_matcher(self, client, db):
        self._seed(db)
        data = client.get("/api/stats?team=A&scope=senaste_n&n=99").json()
        assert data["omfattning"]["antal_matcher"] == 4

    def test_hela_sasongen(self, client, db):
        self._seed(db)
        data = client.get("/api/stats?team=A&scope=sasong").json()
        assert data["omfattning"]["antal_matcher"] == 4
        assert rows_by_id(data)[10]["mal"] == 4


# ---------------------------------------------------------------------------
# Lagseparation
# ---------------------------------------------------------------------------

class TestLagseparation:
    def test_pendlare_far_siffror_per_lag(self, client, db):
        add_match(db, 1, "A", datetime(2026, 9, 1))
        add_match(db, 2, "B", datetime(2026, 9, 2))
        add_player(db, 10, "Pendlare", "7")
        add_appearance(db, 1, 10, "Pendlare", goals=3)
        add_appearance(db, 2, 10, "Pendlare", goals=1)
        db.flush()

        a = rows_by_id(client.get("/api/stats?team=A").json())[10]
        b = rows_by_id(client.get("/api/stats?team=B").json())[10]
        assert a["mal"] == 3
        assert a["matcher"] == 1
        assert b["mal"] == 1
        assert b["matcher"] == 1


# ---------------------------------------------------------------------------
# Skottstatistik
# ---------------------------------------------------------------------------

class TestSkott:
    def test_skott_totalt_och_andelar_summerar_till_100(self, client, db):
        add_match(db, 1, "A", datetime(2026, 9, 1))
        add_player(db, 10, "Kalle", "7")
        add_appearance(db, 1, 10, "Kalle", goals=2)
        # 2 mål + 4 på mål + 1 utanför + 1 i täck = 8 totalt
        add_shot(db, "s1", 1, 10, "on_goal")
        add_shot(db, "s2", 1, 10, "on_goal")
        add_shot(db, "s3", 1, 10, "on_goal")
        add_shot(db, "s4", 1, 10, "on_goal")
        add_shot(db, "s5", 1, 10, "missed")
        add_shot(db, "s6", 1, 10, "blocked")
        db.flush()

        skott = rows_by_id(client.get("/api/stats?team=A").json())[10]["skott"]
        assert skott["registrerat"] is True
        assert skott["totalt"] == 8
        assert skott["mal"]["antal"] == 2
        assert skott["pa_mal"]["antal"] == 4
        assert skott["utanfor"]["antal"] == 1
        assert skott["i_tack"]["antal"] == 1
        summa = sum(
            skott[k]["andel"] for k in ("mal", "pa_mal", "utanfor", "i_tack")
        )
        assert summa == 100

    def test_andelar_med_udda_fordelning_summerar_till_100(self, client, db):
        add_match(db, 1, "A", datetime(2026, 9, 1))
        add_player(db, 10, "Kalle", "7")
        add_appearance(db, 1, 10, "Kalle", goals=1)
        add_shot(db, "s1", 1, 10, "on_goal")
        add_shot(db, "s2", 1, 10, "missed")
        db.flush()

        skott = rows_by_id(client.get("/api/stats?team=A").json())[10]["skott"]
        # 1/1/1/0 av 3 → 33/33/34/0 (största rest)
        assert skott["totalt"] == 3
        andelar = [skott[k]["andel"] for k in ("mal", "pa_mal", "utanfor", "i_tack")]
        assert sum(andelar) == 100
        assert sorted(andelar) == [0, 33, 33, 34]

    def test_ingen_registrering_ger_tomma_skottfalt_inte_noll(self, client, db):
        add_match(db, 1, "A", datetime(2026, 9, 1))
        add_player(db, 10, "Kalle", "7")
        add_appearance(db, 1, 10, "Kalle", goals=1)
        db.flush()

        row = rows_by_id(client.get("/api/stats?team=A").json())[10]
        assert row["skott"] == {"registrerat": False}
        # iBIS-siffrorna finns kvar
        assert row["mal"] == 1

    def test_skott_raknas_bara_over_registrerade_matcher(self, client, db):
        # Två matcher. Bara match 1 har skottregistrering. Målandelen i
        # skottbreddningen ska bara räkna match 1:s mål, så helheten hänger ihop.
        add_match(db, 1, "A", datetime(2026, 9, 1))
        add_match(db, 2, "A", datetime(2026, 9, 8))
        add_player(db, 10, "Kalle", "7")
        add_appearance(db, 1, 10, "Kalle", goals=1)
        add_appearance(db, 2, 10, "Kalle", goals=5)
        add_shot(db, "s1", 1, 10, "on_goal")
        db.flush()

        row = rows_by_id(client.get("/api/stats?team=A").json())[10]
        assert row["mal"] == 6  # toppsiffran över hela omfattningen
        assert row["skott"]["registrerat"] is True
        assert row["skott"]["mal"]["antal"] == 1  # bara match 1
        assert row["skott"]["totalt"] == 2  # 1 mål + 1 på mål

    def test_tombstonad_skotthandelse_raknas_inte(self, client, db):
        add_match(db, 1, "A", datetime(2026, 9, 1))
        add_player(db, 10, "Kalle", "7")
        add_appearance(db, 1, 10, "Kalle", goals=0)
        add_shot(db, "s1", 1, 10, "on_goal")
        add_shot(db, "s2", 1, 10, "on_goal",
                 deleted_at=datetime(2026, 9, 1, 20, 0))
        db.flush()

        skott = rows_by_id(client.get("/api/stats?team=A").json())[10]["skott"]
        assert skott["registrerat"] is True
        assert skott["pa_mal"]["antal"] == 1
        assert skott["totalt"] == 1

    def test_motstandarens_skott_markerar_inte_matchen_som_registrerad(self, client, db):
        # Bara motståndarens skott är registrerat i matchen. Våra spelares
        # skottfält ska vara tomma (registrerat=False), inte noll (SPEC 6.1/7).
        add_match(db, 1, "A", datetime(2026, 9, 1))
        add_player(db, 10, "Kalle", "7")
        add_appearance(db, 1, 10, "Kalle", goals=1)
        add_shot(db, "o1", 1, None, "on_goal", side="motstandare")
        db.flush()

        row = rows_by_id(client.get("/api/stats?team=A").json())[10]
        assert row["skott"] == {"registrerat": False}

    def test_registrerad_match_men_spelaren_utan_skott_ger_nollor(self, client, db):
        # Match 1 har registrering (för en annan spelare). Kalle stod i truppen
        # men fick inget – det är genuint noll, inte saknad data.
        add_match(db, 1, "A", datetime(2026, 9, 1))
        add_player(db, 10, "Kalle", "7")
        add_player(db, 20, "Olle", "8")
        add_appearance(db, 1, 10, "Kalle", goals=0)
        add_appearance(db, 1, 20, "Olle", goals=0)
        add_shot(db, "s1", 1, 20, "on_goal")
        db.flush()

        skott = rows_by_id(client.get("/api/stats?team=A").json())[10]["skott"]
        assert skott["registrerat"] is True
        assert skott["totalt"] == 0
        assert skott["mal"]["antal"] == 0
        assert skott["mal"]["andel"] is None


# ---------------------------------------------------------------------------
# Roster edits slår igenom (SPEC 6.5)
# ---------------------------------------------------------------------------

class TestRosterEdits:
    def test_borttagen_spelare_forsvinner_ur_statistiken(self, client, db):
        add_match(db, 1, "A", datetime(2026, 9, 1))
        add_player(db, 10, "Kalle", "7")
        add_appearance(db, 1, 10, "Kalle", goals=2)
        add_roster_edit(db, 1, 10, "remove")
        db.flush()

        assert client.get("/api/stats?team=A").json()["spelare"] == []

    def test_tillagd_spelare_kommer_med(self, client, db):
        add_match(db, 1, "A", datetime(2026, 9, 1))
        add_player(db, 10, "Kalle", "7")
        add_player(db, 20, "Glömd", "8")
        add_appearance(db, 1, 10, "Kalle", goals=1)
        db.add(PlayerTeam(player_id=20, team="A"))
        add_roster_edit(db, 1, 20, "add")
        db.flush()

        row = rows_by_id(client.get("/api/stats?team=A").json())[20]
        assert row["matcher"] == 1
        assert row["mal"] == 0  # ingen appearance, inga iBIS-siffror
        assert row["skott"] == {"registrerat": False}
