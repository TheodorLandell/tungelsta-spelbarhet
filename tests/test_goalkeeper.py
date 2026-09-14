"""
Tester för målvaktsstatistik (SPEC 6.8).

Motståndarens skott bär själva vilken målvakt som stod – den attribueringen är
exakt. Insläppta mål kommer från iBIS med bara period, och tilldelas därför den
målvakt som mötte flest skott i perioden. Byts målvakt mitt i en period är det
en approximation som ska framgå i gränssnittet.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import app, get_db, _clear_status_cache
from app.auth import require_session
from app.goalkeeper import attribute_conceded, save_stats
from app.models import Appearance, Base, Match, Player, ShotEvent

# Lag A i testkonfigurationen. Matchen spelas hemma, så motståndaren är borta.
TEAM_A_ID = 1977


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


def add_match(db, match_id=1, *, egna_mal=2, motstandar_mal=3, perioder=None):
    """Vi är hemmalag. motstandar_mal är alltså bortalagets mål."""
    raw = {
        "HomeTeamID": TEAM_A_ID,
        "AwayTeamID": 9999,
        "GoalsHomeTeam": egna_mal,
        "GoalsAwayTeam": motstandar_mal,
        "CompetitionTypeID": 1,
    }
    if perioder is not None:
        raw["IntermediateResults"] = [
            {"Period": p, "GoalsHomeTeam": 0, "GoalsAwayTeam": n}
            for p, n in perioder.items()
        ]
    db.add(Match(
        match_id=match_id, team="A", competition_id=100,
        kickoff=datetime(2026, 9, 1, 19), status="played",
        opponent="Motståndarna", raw=raw,
    ))


def add_gk(db, player_id, namn):
    db.add(Player(
        player_id=player_id, name=namn, shirt_no="1",
        is_goalkeeper=True, last_seen=datetime(2026, 9, 1),
    ))


def add_appearance(db, match_id, player_id, namn):
    db.add(Appearance(
        match_id=match_id, player_id=player_id, player_name=namn,
        shirt_no="1", goals=0, assists=0, penalty_minutes=0,
    ))


_n = [0]


def add_opponent_shot(db, match_id, kind, period, gk_id, *, deleted_at=None):
    _n[0] += 1
    db.add(ShotEvent(
        id=f"o{_n[0]}", match_id=match_id, player_id=None,
        goalkeeper_id=gk_id, side="motstandare", kind=kind, period=period,
        created_at=datetime(2026, 9, 1, 19, 30), created_by="Theo",
        deleted_at=deleted_at,
    ))


def gk_row(data, player_id):
    return next(p for p in data["spelare"] if p["player_id"] == player_id)


# ---------------------------------------------------------------------------
# Ren attribueringslogik
# ---------------------------------------------------------------------------

class TestAttributeConceded:
    def test_en_malvakt_hela_matchen_far_alla_mal(self):
        events = {1: {10: 4}, 2: {10: 3}, 3: {10: 5}}
        per_gk, oattr, approx = attribute_conceded(events, {1: 1, 2: 0, 3: 2})

        assert per_gk == {10: 3}
        assert oattr == 0
        assert approx is False

    def test_byte_mellan_perioder_delar_malen(self):
        # Målvakt 10 stod period 1-2, målvakt 20 period 3.
        events = {1: {10: 4}, 2: {10: 3}, 3: {20: 5}}
        per_gk, oattr, approx = attribute_conceded(events, {1: 1, 2: 1, 3: 2})

        assert per_gk == {10: 2, 20: 2}
        assert oattr == 0
        # Ingen period delades, så inget är approximativt.
        assert approx is False

    def test_byte_mitt_i_period_ger_flest_skott_och_markeras(self):
        # Båda stod i period 2. 20 mötte flest skott och får periodens mål.
        events = {1: {10: 4}, 2: {10: 1, 20: 6}, 3: {20: 5}}
        per_gk, oattr, approx = attribute_conceded(events, {1: 0, 2: 3, 3: 1})

        assert per_gk == {20: 4}
        assert oattr == 0
        assert approx is True

    def test_period_utan_registrering_ger_oattribuerat(self):
        events = {1: {10: 4}}
        per_gk, oattr, approx = attribute_conceded(events, {1: 1, 2: 2, 3: 0})

        assert per_gk == {10: 1}
        assert oattr == 2
        assert approx is False

    def test_mal_utan_period_ar_oattribuerat(self):
        events = {1: {10: 4}}
        per_gk, oattr, _ = attribute_conceded(events, {1: 1}, conceded_unknown=3)

        assert per_gk == {10: 1}
        assert oattr == 3

    def test_summan_gar_alltid_ihop(self):
        events = {1: {10: 2}, 3: {20: 1}}
        conceded = {1: 2, 2: 4, 3: 1}
        per_gk, oattr, _ = attribute_conceded(events, conceded, conceded_unknown=2)

        assert sum(per_gk.values()) + oattr == sum(conceded.values()) + 2

    def test_lika_manga_skott_ger_samma_agare_oavsett_ordning(self):
        a, _, _ = attribute_conceded({2: {10: 3, 20: 3}}, {2: 1})
        b, _, _ = attribute_conceded({2: {20: 3, 10: 3}}, {2: 1})
        assert a == b == {10: 1}


class TestSaveStats:
    def test_raddningar_och_procent(self):
        # 8 registrerade skott på mål (alltså räddningar) och 2 insläppta.
        s = save_stats(8, 2)
        assert s["skott_pa_mal_mot"] == 10
        assert s["inslappta"] == 2
        assert s["raddningar"] == 8
        assert s["raddningsprocent"] == 80

    def test_noll_skott_kraschar_inte(self):
        s = save_stats(0, 0)
        assert s["skott_pa_mal_mot"] == 0
        assert s["raddningar"] == 0
        assert s["raddningsprocent"] is None

    def test_bara_inslappta_ger_noll_procent(self):
        s = save_stats(0, 3)
        assert s["skott_pa_mal_mot"] == 3
        assert s["raddningar"] == 0
        assert s["raddningsprocent"] == 0

    def test_raddningar_kan_aldrig_bli_negativa(self):
        # Målen ingår i skott på mål mot, så differensen är alltid räddningarna.
        for reg in range(0, 6):
            for mal in range(0, 6):
                s = save_stats(reg, mal)
                assert s["raddningar"] >= 0
                assert s["raddningar"] == s["skott_pa_mal_mot"] - s["inslappta"]


# ---------------------------------------------------------------------------
# Statistiksidan
# ---------------------------------------------------------------------------

class TestMalvaktsstatistikPaStatistiksidan:
    def test_skott_tilldelas_den_valda_malvakten(self, client, db):
        add_match(db, 1, motstandar_mal=0, perioder={1: 0, 2: 0, 3: 0})
        add_gk(db, 10, "Målvakt A")
        add_gk(db, 20, "Målvakt B")
        add_appearance(db, 1, 10, "Målvakt A")
        add_appearance(db, 1, 20, "Målvakt B")
        for _ in range(5):
            add_opponent_shot(db, 1, "on_goal", 1, 10)
        db.flush()

        data = client.get("/api/stats?team=A").json()

        a = gk_row(data, 10)["malvaktsstatistik"]
        assert a["registrerat"] is True
        assert a["skott_pa_mal_mot"] == 5
        assert a["raddningar"] == 5

        # Målvakt B mötte inga skott – tomt, inte nollor.
        assert gk_row(data, 20)["malvaktsstatistik"] == {"registrerat": False}

    def test_byte_mitt_i_matchen_lagger_skotten_pa_den_nya(self, client, db):
        add_match(db, 1, motstandar_mal=0, perioder={1: 0, 2: 0, 3: 0})
        add_gk(db, 10, "Målvakt A")
        add_gk(db, 20, "Målvakt B")
        add_appearance(db, 1, 10, "Målvakt A")
        add_appearance(db, 1, 20, "Målvakt B")
        # A står period 1-2, B tar över i period 3.
        for _ in range(3):
            add_opponent_shot(db, 1, "on_goal", 1, 10)
        for _ in range(2):
            add_opponent_shot(db, 1, "on_goal", 2, 10)
        for _ in range(4):
            add_opponent_shot(db, 1, "on_goal", 3, 20)
        db.flush()

        data = client.get("/api/stats?team=A").json()

        assert gk_row(data, 10)["malvaktsstatistik"]["skott_pa_mal_mot"] == 5
        assert gk_row(data, 20)["malvaktsstatistik"]["skott_pa_mal_mot"] == 4

    def test_inslappta_mal_foljer_perioden(self, client, db):
        # Motståndaren gör 1 mål i period 1 och 2 i period 3.
        add_match(db, 1, motstandar_mal=3, perioder={1: 1, 2: 0, 3: 2})
        add_gk(db, 10, "Målvakt A")
        add_gk(db, 20, "Målvakt B")
        add_appearance(db, 1, 10, "Målvakt A")
        add_appearance(db, 1, 20, "Målvakt B")
        for _ in range(6):
            add_opponent_shot(db, 1, "on_goal", 1, 10)
        for _ in range(4):
            add_opponent_shot(db, 1, "on_goal", 3, 20)
        db.flush()

        data = client.get("/api/stats?team=A").json()

        a = gk_row(data, 10)["malvaktsstatistik"]
        b = gk_row(data, 20)["malvaktsstatistik"]

        # A: 6 räddningar + 1 insläppt = 7 på mål mot
        assert a["inslappta"] == 1
        assert a["skott_pa_mal_mot"] == 7
        assert a["raddningar"] == 6
        assert a["raddningsprocent"] == 86

        # B: 4 räddningar + 2 insläppta = 6 på mål mot
        assert b["inslappta"] == 2
        assert b["skott_pa_mal_mot"] == 6
        assert b["raddningar"] == 4
        assert b["raddningsprocent"] == 67

    def test_utespelare_far_ingen_malvaktsstatistik(self, client, db):
        add_match(db, 1, motstandar_mal=0, perioder={1: 0, 2: 0, 3: 0})
        db.add(Player(
            player_id=30, name="Utespelare", shirt_no="7",
            is_goalkeeper=False, last_seen=datetime(2026, 9, 1),
        ))
        add_appearance(db, 1, 30, "Utespelare")
        db.flush()

        rad = gk_row(client.get("/api/stats?team=A").json(), 30)
        assert rad["malvaktsstatistik"] is None

    def test_skott_utan_malvakt_raknas_inte(self, client, db):
        # Registrerade innan målvaktsvalet fanns – hellre tomt än gissat.
        add_match(db, 1, motstandar_mal=0, perioder={1: 0, 2: 0, 3: 0})
        add_gk(db, 10, "Målvakt A")
        add_appearance(db, 1, 10, "Målvakt A")
        for _ in range(4):
            add_opponent_shot(db, 1, "on_goal", 1, None)
        db.flush()

        rad = gk_row(client.get("/api/stats?team=A").json(), 10)
        assert rad["malvaktsstatistik"] == {"registrerat": False}

    def test_tombstonat_skott_raknas_inte(self, client, db):
        add_match(db, 1, motstandar_mal=0, perioder={1: 0, 2: 0, 3: 0})
        add_gk(db, 10, "Målvakt A")
        add_appearance(db, 1, 10, "Målvakt A")
        add_opponent_shot(db, 1, "on_goal", 1, 10)
        add_opponent_shot(db, 1, "on_goal", 1, 10,
                          deleted_at=datetime(2026, 9, 1, 20))
        db.flush()

        rad = gk_row(client.get("/api/stats?team=A").json(), 10)
        assert rad["malvaktsstatistik"]["raddningar"] == 1

    def test_bara_utanfor_och_i_tack_ger_noll_pa_mal_men_registrerat(self, client, db):
        # Målvakten mötte skott, men inga på mål. Det är genuint noll.
        add_match(db, 1, motstandar_mal=0, perioder={1: 0, 2: 0, 3: 0})
        add_gk(db, 10, "Målvakt A")
        add_appearance(db, 1, 10, "Målvakt A")
        add_opponent_shot(db, 1, "missed", 1, 10)
        add_opponent_shot(db, 1, "blocked", 1, 10)
        db.flush()

        s = gk_row(client.get("/api/stats?team=A").json(), 10)["malvaktsstatistik"]
        assert s["registrerat"] is True
        assert s["skott_pa_mal_mot"] == 0
        assert s["raddningsprocent"] is None

    def test_byte_mitt_i_period_markeras_som_approximativt(self, client, db):
        add_match(db, 1, motstandar_mal=2, perioder={1: 0, 2: 2, 3: 0})
        add_gk(db, 10, "Målvakt A")
        add_gk(db, 20, "Målvakt B")
        add_appearance(db, 1, 10, "Målvakt A")
        add_appearance(db, 1, 20, "Målvakt B")
        add_opponent_shot(db, 1, "on_goal", 2, 10)
        for _ in range(5):
            add_opponent_shot(db, 1, "on_goal", 2, 20)
        db.flush()

        data = client.get("/api/stats?team=A").json()
        assert gk_row(data, 20)["malvaktsstatistik"]["inslappta"] == 2
        assert gk_row(data, 20)["malvaktsstatistik"]["approximativ"] is True


# ---------------------------------------------------------------------------
# Skottsynken bär målvakten
# ---------------------------------------------------------------------------

class TestShotEventGoalkeeper:
    def _post(self, client, **kw):
        h = {
            "id": "uuid-gk-1", "side": "motstandare", "kind": "on_goal",
            "period": 2, "created_at": "2026-09-01T19:30:00Z",
            "created_by": "Theo",
        }
        h.update(kw)
        return client.post("/api/matches/1/shot-events", json={"handelser": [h]})

    def test_malvakt_sparas_och_kommer_tillbaka(self, client, db):
        add_match(db, 1)
        add_gk(db, 10, "Målvakt A")
        db.flush()

        assert self._post(client, goalkeeper_id=10).status_code == 200

        rows = client.get("/api/matches/1/shot-events").json()["handelser"]
        assert rows[0]["goalkeeper_id"] == 10

    def test_eget_skott_far_aldrig_malvakt(self, client, db):
        add_match(db, 1)
        add_gk(db, 10, "Målvakt A")
        db.add(Player(
            player_id=30, name="Utespelare", shirt_no="7",
            is_goalkeeper=False, last_seen=datetime(2026, 9, 1),
        ))
        db.flush()

        assert self._post(
            client, side="egen", player_id=30, goalkeeper_id=10
        ).status_code == 200

        rows = client.get("/api/matches/1/shot-events").json()["handelser"]
        assert rows[0]["goalkeeper_id"] is None

    def test_handelse_utan_faltet_tas_emot(self, client, db):
        # Äldre klient som inte känner till målvakter.
        add_match(db, 1)
        db.flush()

        assert self._post(client).status_code == 200
        rows = client.get("/api/matches/1/shot-events").json()["handelser"]
        assert rows[0]["goalkeeper_id"] is None
