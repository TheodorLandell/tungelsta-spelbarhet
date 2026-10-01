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
    def test_mal_ingar_i_pa_mal_och_dubbelraknas_inte(self, client, db):
        # Ett mål är ett skott på mål (SPEC 7). 2 mål + 4 registrerade skott på
        # mål = 6 på mål. Totalen är 6 + 1 + 1 = 8, inte 10 – målen räknas bara
        # en gång.
        add_match(db, 1, "A", datetime(2026, 9, 1))
        add_player(db, 10, "Kalle", "7")
        add_appearance(db, 1, 10, "Kalle", goals=2)
        add_shot(db, "s1", 1, 10, "on_goal")
        add_shot(db, "s2", 1, 10, "on_goal")
        add_shot(db, "s3", 1, 10, "on_goal")
        add_shot(db, "s4", 1, 10, "on_goal")
        add_shot(db, "s5", 1, 10, "missed")
        add_shot(db, "s6", 1, 10, "blocked")
        db.flush()

        skott = rows_by_id(client.get("/api/stats?team=A").json())[10]["skott"]
        assert skott["registrerat"] is True
        assert skott["pa_mal"]["antal"] == 6      # 4 registrerade + 2 mål
        assert skott["utanfor"]["antal"] == 1
        assert skott["i_tack"]["antal"] == 1
        assert skott["totalt"] == 8               # inte 10
        assert skott["totalt"] == (
            skott["pa_mal"]["antal"]
            + skott["utanfor"]["antal"]
            + skott["i_tack"]["antal"]
        )
        # Mål visas som eget värde, utan att vara en egen post i totalen.
        assert skott["mal"] == 2
        assert skott["malprocent"] == 33          # 2 / 6

    def test_de_tre_andelarna_summerar_till_100(self, client, db):
        add_match(db, 1, "A", datetime(2026, 9, 1))
        add_player(db, 10, "Kalle", "7")
        add_appearance(db, 1, 10, "Kalle", goals=2)
        add_shot(db, "s1", 1, 10, "on_goal")
        add_shot(db, "s2", 1, 10, "on_goal")
        add_shot(db, "s3", 1, 10, "on_goal")
        add_shot(db, "s4", 1, 10, "on_goal")
        add_shot(db, "s5", 1, 10, "missed")
        add_shot(db, "s6", 1, 10, "blocked")
        db.flush()

        skott = rows_by_id(client.get("/api/stats?team=A").json())[10]["skott"]
        andelar = [skott[k]["andel"] for k in ("pa_mal", "utanfor", "i_tack")]
        assert sum(andelar) == 100
        # 6/1/1 av 8 → 75 / 12,5 / 12,5. Största rest ger den udda procenten
        # till den första av de två lika.
        assert andelar == [75, 13, 12]
        # Mål är ingen andel längre – bara ett värde.
        assert not isinstance(skott["mal"], dict)

    def test_andelar_med_udda_fordelning_summerar_till_100(self, client, db):
        add_match(db, 1, "A", datetime(2026, 9, 1))
        add_player(db, 10, "Kalle", "7")
        add_appearance(db, 1, 10, "Kalle", goals=1)
        add_shot(db, "s1", 1, 10, "on_goal")
        add_shot(db, "s2", 1, 10, "missed")
        db.flush()

        skott = rows_by_id(client.get("/api/stats?team=A").json())[10]["skott"]
        # 2 på mål (1 registrerat + 1 mål), 1 utanför, 0 i täck av 3 → 67/33/0
        assert skott["totalt"] == 3
        andelar = [skott[k]["andel"] for k in ("pa_mal", "utanfor", "i_tack")]
        assert sum(andelar) == 100
        assert andelar == [67, 33, 0]

    def test_malprocent_ar_none_utan_skott_pa_mal(self, client, db):
        add_match(db, 1, "A", datetime(2026, 9, 1))
        add_player(db, 10, "Kalle", "7")
        add_appearance(db, 1, 10, "Kalle", goals=0)
        add_shot(db, "s1", 1, 10, "missed")
        db.flush()

        skott = rows_by_id(client.get("/api/stats?team=A").json())[10]["skott"]
        assert skott["pa_mal"]["antal"] == 0
        assert skott["mal"] == 0
        assert skott["malprocent"] is None

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
        assert row["skott"]["mal"] == 1  # bara match 1
        # 1 registrerat skott på mål + 1 mål = 2 på mål, som är hela totalen
        assert row["skott"]["pa_mal"]["antal"] == 2
        assert row["skott"]["totalt"] == 2

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
        assert skott["mal"] == 0
        assert skott["malprocent"] is None
        assert skott["pa_mal"]["andel"] is None


# ---------------------------------------------------------------------------
# Verklig match: IFK Haninge (C) - Tungelsta IF (B) 6-8, match_id 1723835
# ---------------------------------------------------------------------------

# Spelarnas mål från iBIS lineups för den matchen. Siffrorna är verifierade mot
# både lineups och matchhändelserna – de stämmer överens spelare för spelare.
MATCH_1723835_MAL = {
    132951: ("Johnny Andersson", 2),
    480798: ("William Lindahl", 3),
    490161: ("Adam Burgren", 1),
    490139: ("Felix Wikström", 1),
    538277: ("Tim Johannesson", 1),
    205819: ("Martin Midelf", 0),
    47205: ("Niklas Sandborg", 0),
}

# Registrerade skott under matchen. Johnny har 1 registrerat skott på mål och
# 2 mål, vilket ska ge 3 på mål – exakt fallet som såg fel ut i statistikvyn.
MATCH_1723835_SKOTT = {
    132951: {"on_goal": 1, "missed": 3, "blocked": 4},
    480798: {"on_goal": 2, "missed": 1, "blocked": 1},
    490161: {"on_goal": 4, "missed": 2, "blocked": 0},
    490139: {"on_goal": 0, "missed": 1, "blocked": 2},
    538277: {"on_goal": 3, "missed": 0, "blocked": 1},
    205819: {"on_goal": 2, "missed": 2, "blocked": 1},
    47205: {"on_goal": 0, "missed": 0, "blocked": 0},
}


class TestVerkligMatch1723835:
    def _seed(self, db):
        add_match(db, 1723835, "B", datetime(2026, 9, 14, 20))
        n = 0
        for pid, (namn, mal) in MATCH_1723835_MAL.items():
            add_player(db, pid, namn, str(pid)[:2])
            add_appearance(db, 1723835, pid, namn, goals=mal)
            for kind, antal in MATCH_1723835_SKOTT[pid].items():
                for _ in range(antal):
                    n += 1
                    add_shot(db, f"s{n}", 1723835, pid, kind)
        db.flush()

    def test_varje_spelares_pa_mal_inkluderar_hans_mal(self, client, db):
        self._seed(db)
        rader = rows_by_id(client.get("/api/stats?team=B").json())

        for pid, (namn, mal) in MATCH_1723835_MAL.items():
            skott = rader[pid]["skott"]
            registrerade = MATCH_1723835_SKOTT[pid]["on_goal"]

            assert skott["pa_mal"]["antal"] == registrerade + mal, namn
            assert skott["pa_mal"]["antal"] >= mal, namn
            assert skott["mal"] == mal, namn

    def test_johnny_andersson_har_tre_pa_mal_och_67_procents_malprocent(
        self, client, db
    ):
        # Fallet ur statistikvyn: 2 mål, 1 registrerat skott på mål.
        self._seed(db)
        rad = rows_by_id(client.get("/api/stats?team=B").json())[132951]

        assert rad["mal"] == 2
        assert rad["skott"]["pa_mal"]["antal"] == 3     # 1 registrerat + 2 mål
        assert rad["skott"]["mal"] == 2
        assert rad["skott"]["malprocent"] == 67         # 2 av 3
        # Totalt = 3 på mål + 3 utanför + 4 i täck
        assert rad["skott"]["totalt"] == 10

    def test_totalen_ar_pa_mal_plus_utanfor_plus_i_tack(self, client, db):
        self._seed(db)
        rader = rows_by_id(client.get("/api/stats?team=B").json())

        for pid, (namn, _mal) in MATCH_1723835_MAL.items():
            skott = rader[pid]["skott"]
            assert skott["totalt"] == (
                skott["pa_mal"]["antal"]
                + skott["utanfor"]["antal"]
                + skott["i_tack"]["antal"]
            ), namn

    def test_de_tre_andelarna_summerar_till_100_for_varje_spelare(self, client, db):
        self._seed(db)
        rader = rows_by_id(client.get("/api/stats?team=B").json())

        for pid, (namn, _mal) in MATCH_1723835_MAL.items():
            skott = rader[pid]["skott"]
            andelar = [skott[k]["andel"] for k in ("pa_mal", "utanfor", "i_tack")]
            if skott["totalt"] == 0:
                # Ingen fördelning att göra – inga andelar alls.
                assert andelar == [None, None, None], namn
            else:
                assert sum(andelar) == 100, f"{namn}: {andelar}"

    def test_mal_ar_inte_en_egen_post_i_fordelningen(self, client, db):
        self._seed(db)
        rad = rows_by_id(client.get("/api/stats?team=B").json())[132951]

        # Mål är ett värde, inte ett segment med antal och andel.
        assert not isinstance(rad["skott"]["mal"], dict)
        assert "andel" not in str(rad["skott"]["mal"])


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


# ---------------------------------------------------------------------------
# Borttagen ur iBIS-truppen (synken speglar iBIS)
# ---------------------------------------------------------------------------

class TestBorttagenUrTruppen:
    """
    Tas en spelare bort ur matchtruppen i iBIS raderar synken hans appearance
    för den matchen. Skotten han hunnit få registrerade ligger kvar i
    shot_events – de är en tränares inmatning och raderas aldrig av en synk –
    men de räknas inte i statistiken, eftersom han inte längre står i truppen.
    """

    def test_skott_i_matchen_raknas_inte_utan_appearance(self, client, db):
        add_match(db, 1, "A", datetime(2026, 9, 1))
        add_player(db, 10, "Kalle", "7")
        # Ingen appearance för 10: iBIS har plockat bort honom ur truppen.
        add_shot(db, "s1", 1, 10, "on_goal")
        add_shot(db, "s2", 1, 10, "missed")
        db.flush()

        assert client.get("/api/stats?team=A").json()["spelare"] == []
        # Raderna finns kvar i databasen, de räknas bara inte.
        assert db.get(ShotEvent, "s1") is not None
        assert db.get(ShotEvent, "s2") is not None

    def test_ovriga_matcher_raknas_fortfarande(self, client, db):
        add_match(db, 1, "A", datetime(2026, 9, 1))
        add_match(db, 2, "A", datetime(2026, 9, 8))
        add_player(db, 10, "Kalle", "7")
        # Kvar i match 1, borttagen ur match 2.
        add_appearance(db, 1, 10, "Kalle", goals=1)
        add_shot(db, "s1", 1, 10, "on_goal")
        add_shot(db, "s2", 2, 10, "on_goal")
        add_shot(db, "s3", 2, 10, "blocked")
        db.flush()

        row = rows_by_id(client.get("/api/stats?team=A").json())[10]
        assert row["matcher"] == 1
        assert row["mal"] == 1
        # Ett registrerat skott på mål plus målet, inget från match 2.
        assert row["skott"]["totalt"] == 2
        assert row["skott"]["pa_mal"]["antal"] == 2
        assert row["skott"]["i_tack"]["antal"] == 0

    def test_manuellt_tillagd_igen_raknar_skotten(self, client, db):
        """
        Rättar tränaren iBIS-felet med Ändra matchlista står spelaren i truppen
        igen – och då räknas skotten han redan hunnit få registrerade.
        """
        add_match(db, 1, "A", datetime(2026, 9, 1))
        add_player(db, 10, "Kalle", "7")
        db.add(PlayerTeam(player_id=10, team="A"))
        add_shot(db, "s1", 1, 10, "on_goal")
        add_shot(db, "s2", 1, 10, "missed")
        add_roster_edit(db, 1, 10, "add")
        db.flush()

        row = rows_by_id(client.get("/api/stats?team=A").json())[10]
        assert row["matcher"] == 1
        assert row["skott"]["totalt"] == 2
        assert row["skott"]["pa_mal"]["antal"] == 1
        assert row["skott"]["utanfor"]["antal"] == 1
