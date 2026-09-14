"""
Tester för matchhändelser och perioduppdelning (SPEC 6.7).

Ett mål ska bara räknas i den period det gjordes i. Källan är Events[] i
matchobjektet, som är det enda stället perioden finns. Saknas perioden räknas
målet bara i "hela matchen" – hellre en saknad siffra än en felaktig.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import app, get_db, _clear_status_cache
from app.auth import require_session
from app.ibis_client import parse_match_events, penalty_minutes_from_name
from app.models import Appearance, Base, Match, MatchEvent, Player
from app.periods import split_by_period, team_periods_from_raw


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


# TeamID 1977 = lag A. Matchen nedan spelas hemma, så vi är hemmalaget.
def add_match(db, match_id=1, team="A", *, intermediate=None, goals=(3, 2)):
    raw = {
        "HomeTeamID": 1977,
        "AwayTeamID": 9999,
        "HomeTeam": "Tungelsta IF (A)",
        "AwayTeam": "Motståndarna",
        "GoalsHomeTeam": goals[0],
        "GoalsAwayTeam": goals[1],
        "CompetitionTypeID": 1,
    }
    if intermediate is not None:
        raw["IntermediateResults"] = intermediate
    db.add(Match(
        match_id=match_id, team=team, competition_id=100,
        kickoff=datetime(2026, 9, 14, 20), status="played",
        opponent="Motståndarna", raw=raw,
    ))


def add_player(db, player_id, name="Spelare", shirt_no="9"):
    db.add(Player(
        player_id=player_id, name=name, shirt_no=shirt_no,
        is_goalkeeper=False, last_seen=datetime(2026, 9, 14),
    ))


def add_appearance(db, match_id, player_id, name="Spelare", *,
                   goals=0, assists=0, penalty_minutes=0):
    db.add(Appearance(
        match_id=match_id, player_id=player_id, player_name=name,
        shirt_no="9", goals=goals, assists=assists,
        penalty_minutes=penalty_minutes,
    ))


def add_event(db, event_id, match_id, player_id, kind, period, *, minutes=None):
    db.add(MatchEvent(
        match_event_id=event_id, match_id=match_id, kind=kind,
        period=period, player_id=player_id, penalty_minutes=minutes,
    ))


def player_row(data, player_id):
    return next(p for p in data["trupp"] if p["player_id"] == player_id)


# ---------------------------------------------------------------------------
# Utvisningsminuter ur PenaltyName
# ---------------------------------------------------------------------------

class TestPenaltyMinutesFromName:
    @pytest.mark.parametrize("namn,minuter", [
        ("Slag, 2 min", 2),                    # iBIS med komma
        ("Hårt spel 2 min", 2),                # iBIS utan komma
        ("Otillåten trängning, 2 min", 2),
        ("Obstruktion, 2 min", 2),
        ("Filmning, 5 min", 5),
        ("Ovarsamt spel med klubban 10 min", 10),
    ])
    def test_laser_minuter_ur_texten(self, namn, minuter):
        assert penalty_minutes_from_name(namn) == minuter

    @pytest.mark.parametrize("namn", [
        None, "", "Matchstraff 1", "Utvisning", "2+10 min",
    ])
    def test_okand_text_ger_none(self, namn):
        # Hellre okänd längd än en gissad. None hamnar som okänd period.
        assert penalty_minutes_from_name(namn) is None


class TestParseMatchEvents:
    def test_tar_bara_mal_och_utvisningar(self):
        raw = {"Events": [
            {"MatchEventID": 1, "MatchEventTypeID": 8, "Period": 1},   # periodstart
            {"MatchEventID": 2, "MatchEventTypeID": 1, "Period": 2,
             "PlayerID": 10},                                          # mål
            {"MatchEventID": 3, "MatchEventTypeID": 2, "Period": 3,
             "PlayerID": 11, "PenaltyName": "Slag, 2 min"},            # utvisning
            {"MatchEventID": 4, "MatchEventTypeID": 10, "Period": 1},  # målvakt in
        ]}
        ev = parse_match_events(raw)
        assert [e.MatchEventID for e in ev] == [2, 3]

    def test_events_null_ger_tom_lista(self):
        # Så ser lag-endpointen ut: fältet finns men är null.
        assert parse_match_events({"Events": None}) == []
        assert parse_match_events({}) == []


# ---------------------------------------------------------------------------
# Perioduppdelningens regler
# ---------------------------------------------------------------------------

class TestSplitByPeriod:
    def test_handelserna_tacker_totalen(self):
        perioder, utan = split_by_period(3, {1: 1, 2: 2})
        assert perioder == {1: 1, 2: 2, 3: 0}
        assert utan == 0

    def test_delvis_tackning_ger_resten_som_okand(self):
        perioder, utan = split_by_period(3, {2: 1})
        assert perioder == {1: 0, 2: 1, 3: 0}
        assert utan == 2

    def test_inga_handelser_ger_allt_som_okand(self):
        perioder, utan = split_by_period(2, {})
        assert perioder == {1: 0, 2: 0, 3: 0}
        assert utan == 2

    def test_handelser_over_totalen_gor_hela_uppdelningen_okand(self):
        # Motsäger händelserna totalen går de inte att lita på. Då är det
        # säkrare att visa siffran bara i hela matchen.
        perioder, utan = split_by_period(1, {1: 1, 3: 2})
        assert perioder == {1: 0, 2: 0, 3: 0}
        assert utan == 1

    def test_period_utanfor_1_till_3_raknas_som_okand(self):
        # Förlängning har ingen plats i periodväljaren.
        perioder, utan = split_by_period(2, {1: 1, 4: 1})
        assert perioder == {1: 1, 2: 0, 3: 0}
        assert utan == 1

    def test_summan_ar_alltid_totalen(self):
        for total, ev in [(0, {}), (5, {1: 2}), (2, {1: 9}), (4, {1: 1, 2: 3})]:
            perioder, utan = split_by_period(total, ev)
            assert sum(perioder.values()) + utan == total


class TestTeamPeriodsFromRaw:
    def test_laser_lagets_mal_per_period(self):
        raw = {"IntermediateResults": [
            {"Period": 1, "GoalsHomeTeam": 0, "GoalsAwayTeam": 1},
            {"Period": 2, "GoalsHomeTeam": 3, "GoalsAwayTeam": 4},
            {"Period": 3, "GoalsHomeTeam": 3, "GoalsAwayTeam": 3},
        ]}
        assert team_periods_from_raw(raw, hemma=True) == {1: 0, 2: 3, 3: 3}
        assert team_periods_from_raw(raw, hemma=False) == {1: 1, 2: 4, 3: 3}

    def test_saknas_ger_none(self):
        assert team_periods_from_raw({}, hemma=True) is None
        assert team_periods_from_raw({"IntermediateResults": []}, hemma=True) is None


# ---------------------------------------------------------------------------
# Matchvyn: mål och utvisningar hamnar i rätt period
# ---------------------------------------------------------------------------

class TestMatchDetailPerioder:
    def test_mal_i_period_2_raknas_bara_dar_och_i_hela_matchen(self, client, db):
        add_match(db)
        add_player(db, 10, "Kalle")
        add_appearance(db, 1, 10, "Kalle", goals=1)
        add_event(db, 900, 1, 10, "goal", 2)
        db.flush()

        row = player_row(client.get("/api/matches/1").json(), 10)

        # Hela matchen: målet finns
        assert row["mal"] == 1
        # Bara i period 2
        assert row["mal_perioder"] == {"1": 0, "2": 1, "3": 0}
        assert row["mal_utan_period"] == 0

    def test_tva_utvisningar_a_2_min_ger_4_utvisningsminuter(self, client, db):
        # Joacim Rastas Costell i match 1723835: en utvisning i period 1 och en
        # i period 3, 2 minuter var.
        add_match(db)
        add_player(db, 118461, "Joacim Rastas Costell")
        add_appearance(db, 1, 118461, "Joacim Rastas Costell", penalty_minutes=4)
        add_event(db, 901, 1, 118461, "penalty", 1, minutes=2)
        add_event(db, 902, 1, 118461, "penalty", 3, minutes=2)
        db.flush()

        row = player_row(client.get("/api/matches/1").json(), 118461)

        assert row["utvisningsminuter"] == 4
        assert row["utv_perioder"] == {"1": 2, "2": 0, "3": 2}
        assert row["utv_utan_period"] == 0

    def test_mal_utan_handelser_raknas_bara_i_hela_matchen(self, client, db):
        # iBIS har inte publicerat Events. Totalen finns, perioden inte.
        add_match(db)
        add_player(db, 10, "Kalle")
        add_appearance(db, 1, 10, "Kalle", goals=2)
        db.flush()

        row = player_row(client.get("/api/matches/1").json(), 10)

        assert row["mal"] == 2
        assert row["mal_perioder"] == {"1": 0, "2": 0, "3": 0}
        assert row["mal_utan_period"] == 2

    def test_delvis_periodinformation_lagger_resten_som_okand(self, client, db):
        add_match(db)
        add_player(db, 10, "Kalle")
        add_appearance(db, 1, 10, "Kalle", goals=3)
        add_event(db, 903, 1, 10, "goal", 1)
        db.flush()

        row = player_row(client.get("/api/matches/1").json(), 10)

        assert row["mal"] == 3
        assert row["mal_perioder"] == {"1": 1, "2": 0, "3": 0}
        assert row["mal_utan_period"] == 2

    def test_ospelad_match_ger_inga_periodsiffror(self, client, db):
        db.add(Match(
            match_id=2, team="A", competition_id=100,
            kickoff=datetime(2026, 9, 21, 20), status="scheduled",
            opponent="Motståndarna",
            raw={"HomeTeamID": 1977, "AwayTeamID": 9999, "CompetitionTypeID": 1},
        ))
        add_player(db, 10, "Kalle")
        add_appearance(db, 2, 10, "Kalle", goals=0)
        db.flush()

        row = player_row(client.get("/api/matches/2").json(), 10)

        # Tom målruta, inte nolla (SPEC 6.2)
        assert row["mal"] is None
        assert row["mal_perioder"] is None


class TestMatchDetailLagperioder:
    def test_lagmal_per_period_kommer_fran_intermediateresults(self, client, db):
        # Vi är hemmalag och vinner 3-2: 1-0, 1-1, 1-1.
        add_match(db, intermediate=[
            {"Period": 1, "GoalsHomeTeam": 1, "GoalsAwayTeam": 0},
            {"Period": 2, "GoalsHomeTeam": 1, "GoalsAwayTeam": 1},
            {"Period": 3, "GoalsHomeTeam": 1, "GoalsAwayTeam": 1},
        ])
        db.flush()

        data = client.get("/api/matches/1").json()

        assert data["mal"] == 3
        assert data["mal_perioder"] == {"1": 1, "2": 1, "3": 1}
        assert data["mal_utan_period"] == 0
        assert data["motstandare_mal"] == 2
        assert data["motstandare_mal_perioder"] == {"1": 0, "2": 1, "3": 1}

    def test_utan_intermediateresults_blir_malen_periodlosa(self, client, db):
        add_match(db)
        db.flush()

        data = client.get("/api/matches/1").json()

        assert data["mal"] == 3
        assert data["mal_perioder"] is None
        assert data["mal_utan_period"] == 3
