"""
Tester för synkjobbet. Inga nätverksanrop – klienten är mockad.
Databasen är en in-memory SQLite-instans per test.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.ibis_client import IBISClient, IBISLineups, IBISTeam
from app.models import (
    Appearance,
    Base,
    Match,
    Player,
    PlayerTeam,
    RosterEdit,
    ShotEvent,
    SyncLog,
)
from app.status import get_statuses
from app.sync import (
    SyncResult,
    _has_appearances,
    _prune_appearances,
    _match_status,
    _now_naive,
    _opponent,
    _upsert_match,
    _upsert_player,
    run_sync,
)

TEAM_A_ID = 1977
TEAM_B_ID = 17541
OTHER_ID = 9999

MOCK_SETTINGS = SimpleNamespace(season_id=44, team_a_id=TEAM_A_ID, team_b_id=TEAM_B_ID)


def soon(days: int = 3) -> str:
    """iBIS-tidssträng för en kommande match `days` dagar fram i tiden."""
    return (_now_naive() + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# Hjälpfunktioner för att bygga testdata
# ---------------------------------------------------------------------------

def make_match_dict(
    match_id: int = 1001,
    home_team_id: int = OTHER_ID,
    away_team_id: int = TEAM_A_ID,
    match_datetime: str = "2020-01-15T19:00:00",
    cancelled: bool = False,
    postponed: bool = False,
    abandoned: bool = False,
    goals_home=None,
    goals_away=None,
    final_result_ts=None,
    round_name: str = "Omgång 1",
    match_time_missing: bool = False,
) -> dict:
    return {
        "MatchID": match_id,
        "CompetitionID": 100,
        "CompetitionTypeID": 1,
        "HomeTeamID": home_team_id,
        "HomeTeam": f"Hemmalag {home_team_id}",
        "AwayTeamID": away_team_id,
        "AwayTeam": f"Bortalag {away_team_id}",
        "MatchDateTime": match_datetime,
        "Cancelled": cancelled,
        "Postponed": postponed,
        "Abandoned": abandoned,
        "GoalsHomeTeam": goals_home,
        "GoalsAwayTeam": goals_away,
        "FinalResultCreatedTS": final_result_ts,
        "Round": 1,
        "RoundName": round_name,
        "MatchStatus": None,
        "MatchTimeMissing": match_time_missing,
    }


def make_team_dict(
    team_id: int,
    match_dicts: list[dict],
    players: list[dict] | None = None,
) -> dict:
    return {
        "TeamID": team_id,
        "Name": f"Lag {team_id}",
        "Competitions": [
            {
                "CompetitionID": 100,
                "CompetitionTypeID": 1,
                "Name": "Testserien",
                "Matches": match_dicts,
            }
        ],
        "Players": players or [],
    }


def make_squad_player_dict(player_id: int, name: str = "Trupp Spelare", shirt_no: int | None = 5) -> dict:
    return {"PlayerID": player_id, "Name": name, "ShirtNo": shirt_no}


def make_lineups_dict(
    match_id: int,
    home_id: int = OTHER_ID,
    away_id: int = TEAM_A_ID,
    home_players: list | None = None,
    away_players: list | None = None,
) -> dict:
    return {
        "MatchID": match_id,
        "HomeTeamID": home_id,
        "AwayTeamID": away_id,
        "HomeTeamPlayers": home_players or [],
        "AwayTeamPlayers": away_players or [],
        "HomeTeamTeamPersons": [],
        "AwayTeamTeamPersons": [],
    }


def make_player_dict(
    player_id: int,
    name: str = "Testspelare",
    shirt_no: str = "9",
    goals=None,
    assists=None,
    penalty_minutes=None,
    position=None,
    position_id=None,
) -> dict:
    return {
        "MatchPlayerID": player_id * 100,
        "PlayerID": player_id,
        "Name": name,
        "ShirtNo": shirt_no,
        "Goals": goals,
        "Assists": assists,
        "PenaltyMinutes": penalty_minutes,
        "Position": position,
        "PositionID": position_id,
        "LicensedAssociationID": 258,
    }


def make_event_dict(
    event_id: int,
    event_type: int,
    period: int | None,
    player_id: int,
    *,
    assist_id: int = 0,
    penalty_name: str = "",
    penalty_code: str = "",
    minute: int = 0,
    second: int = 0,
) -> dict:
    """En rad i Events[]. event_type 1 = mål, 2 = utvisning."""
    return {
        "MatchEventID": event_id,
        "MatchEventTypeID": event_type,
        "Period": period,
        "Minute": minute,
        "Second": second,
        "PlayerID": player_id,
        "PlayerAssistID": assist_id,
        "PenaltyCode": penalty_code,
        "PenaltyName": penalty_name,
    }


def build_client(
    team_a_dict: dict | None = None,
    team_b_dict: dict | None = None,
    lineups_by_id: dict[int, dict] | None = None,
    events_by_id: dict[int, list[dict]] | None = None,
) -> IBISClient:
    client = MagicMock(spec=IBISClient)

    def fetch_team_raw(season_id, team_id):
        if team_id == TEAM_A_ID:
            return team_a_dict or make_team_dict(TEAM_A_ID, [])
        return team_b_dict or make_team_dict(TEAM_B_ID, [])

    def fetch_lineups(match_id):
        data = (lineups_by_id or {}).get(
            match_id, make_lineups_dict(match_id, OTHER_ID, TEAM_A_ID)
        )
        return IBISLineups.model_validate(data)

    def fetch_match_raw(match_id):
        # Events är null i lag-endpointen och fylls först här (SPEC 6.7).
        return {"MatchID": match_id, "Events": (events_by_id or {}).get(match_id)}

    client.fetch_team_raw.side_effect = fetch_team_raw
    client.fetch_lineups.side_effect = fetch_lineups
    client.fetch_match_raw.side_effect = fetch_match_raw
    return client


# ---------------------------------------------------------------------------
# Pytest-fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    MakeSession = sessionmaker(bind=eng, autoflush=True)
    with MakeSession() as session:
        yield session


@pytest.fixture
def client():
    return build_client()


# ---------------------------------------------------------------------------
# Enhetstester för hjälpfunktioner
# ---------------------------------------------------------------------------

class TestMatchStatus:
    def _m(self, **kw):
        from app.ibis_client import IBISMatch
        base = dict(
            MatchID=1, CompetitionID=100, CompetitionTypeID=1,
            HomeTeamID=OTHER_ID, AwayTeamID=TEAM_A_ID,
            MatchDateTime="2020-01-01T19:00:00",
            Cancelled=False, Postponed=False, Abandoned=False,
        )
        return IBISMatch(**{**base, **kw})

    def test_cancelled(self):
        assert _match_status(self._m(Cancelled=True)) == "cancelled"

    def test_played(self):
        assert _match_status(self._m(FinalResultCreatedTS="2020-01-01T21:00:00")) == "played"

    def test_scheduled(self):
        assert _match_status(self._m(MatchDateTime="2099-01-01T19:00:00")) == "scheduled"


class TestOpponent:
    def _m(self, home_id, away_id):
        from app.ibis_client import IBISMatch
        return IBISMatch(
            MatchID=1, CompetitionID=100, CompetitionTypeID=1,
            HomeTeamID=home_id, HomeTeam=f"Hemma {home_id}",
            AwayTeamID=away_id, AwayTeam=f"Borta {away_id}",
            MatchDateTime="2020-01-01T19:00:00",
            Cancelled=False, Postponed=False, Abandoned=False,
        )

    def test_tungelsta_hemma(self):
        m = self._m(TEAM_A_ID, OTHER_ID)
        assert _opponent(m, TEAM_A_ID) == f"Borta {OTHER_ID}"

    def test_tungelsta_borta(self):
        m = self._m(OTHER_ID, TEAM_A_ID)
        assert _opponent(m, TEAM_A_ID) == f"Hemma {OTHER_ID}"

    def test_okant_team_ger_none(self):
        m = self._m(OTHER_ID, 8888)
        assert _opponent(m, TEAM_A_ID) is None


# ---------------------------------------------------------------------------
# Integrationstester för run_sync
# ---------------------------------------------------------------------------

class TestRunSync:
    def test_ny_spelad_match_sparas_med_appearances(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = make_match_dict(1001, goals_home=3, goals_away=2,
                            final_result_ts="2020-01-15T21:00:00")
        lineups = make_lineups_dict(1001, away_players=[make_player_dict(42, "Kalle", "7")])

        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={1001: lineups},
        )
        log = run_sync(db, client)

        assert log.ok is True
        assert log.matches_added == 1

        match = db.get(Match, 1001)
        assert match is not None
        assert match.team == "A"
        assert match.status == "played"

        apps = db.scalars(select(Appearance).where(Appearance.match_id == 1001)).all()
        assert len(apps) == 1
        assert apps[0].player_id == 42
        assert apps[0].shirt_no == "7"

    def test_schemalagd_match_utan_publicerad_trupp_ger_inga_appearances(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        # Lineups hämtas ändå (truppen kan vara publicerad i förväg), men
        # mock-klienten ger en tom trupp om inget annat anges – då sparas inget.
        m = make_match_dict(1002, match_datetime=soon())
        client = build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]))

        log = run_sync(db, client)

        assert log.ok is True
        match = db.get(Match, 1002)
        assert match.status == "scheduled"
        client.fetch_lineups.assert_called_once_with(1002)
        assert db.scalars(select(Appearance).where(Appearance.match_id == 1002)).all() == []

    def test_schemalagd_match_med_publicerad_trupp_ger_appearances(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        # Konkret fall ur buggrapporten: truppen är publicerad i iBIS innan
        # matchen är spelad, och ska sparas så registreringsvyn kan visa den.
        m = make_match_dict(1767137, match_datetime=soon())
        lineups = make_lineups_dict(1767137, away_players=[make_player_dict(42, "Kalle", "7")])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={1767137: lineups},
        )

        log = run_sync(db, client)

        assert log.ok is True
        match = db.get(Match, 1767137)
        assert match.status == "scheduled"
        apps = db.scalars(select(Appearance).where(Appearance.match_id == 1767137)).all()
        assert len(apps) == 1
        assert apps[0].player_id == 42

    def test_schemalagd_match_hamtar_om_trupp_varje_synk(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        # En ännu inte färdigrapporterad match ska hämtas om varje synk, så
        # en ändrad trupp speglas – oavsett om matchen redan är spelad eller ej.
        m = make_match_dict(1009, match_datetime=soon())
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={1009: make_lineups_dict(1009, away_players=[make_player_dict(1)])},
        )
        run_sync(db, client)

        client2 = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={1009: make_lineups_dict(1009, away_players=[make_player_dict(1), make_player_dict(2)])},
        )
        run_sync(db, client2)

        client2.fetch_lineups.assert_called_once_with(1009)
        apps = db.scalars(select(Appearance).where(Appearance.match_id == 1009)).all()
        assert {a.player_id for a in apps} == {1, 2}

    def test_kommande_match_langre_bort_an_sju_dagar_hamtas_inte(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        # Trupper publiceras inte tidigare än sju dagar före kickoff – en match
        # längre bort än så ska inte trigga något lineup-anrop.
        m = make_match_dict(1010, match_datetime=soon(10))
        lineups = make_lineups_dict(1010, away_players=[make_player_dict(5)])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={1010: lineups},
        )

        run_sync(db, client)

        client.fetch_lineups.assert_not_called()
        assert db.scalars(
            select(Appearance).where(Appearance.match_id == 1010)
        ).all() == []
        # Matchraden sparas ändå så den syns i matchlistan.
        assert db.get(Match, 1010) is not None

    def test_kommande_match_inom_sju_dagar_hamtas(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = make_match_dict(1011, match_datetime=soon(3))
        lineups = make_lineups_dict(1011, away_players=[make_player_dict(6, "Kalle", "7")])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={1011: lineups},
        )

        run_sync(db, client)

        client.fetch_lineups.assert_called_once_with(1011)
        apps = db.scalars(select(Appearance).where(Appearance.match_id == 1011)).all()
        assert len(apps) == 1
        assert apps[0].player_id == 6

    def test_instaelld_match_sparas_som_cancelled(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = make_match_dict(1003, cancelled=True)
        client = build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]))

        run_sync(db, client)

        match = db.get(Match, 1003)
        assert match.status == "cancelled"
        client.fetch_lineups.assert_not_called()

    def test_redan_komplett_match_hoppar_lineups(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        # Förbered match + appearances i DB. stats_final_ts visar att
        # statistiken hämtades efter slutrapporten – då finns inget nytt att
        # hämta (SPEC 3.5).
        db.add(Match(match_id=1004, team="A", competition_id=100,
                     kickoff=datetime(2020, 1, 15, 19), status="played", raw={},
                     stats_final_ts="2020-01-15T21:00:00"))
        db.add(Player(player_id=42, name="Kalle", last_seen=datetime(2020, 1, 15, 19)))
        db.add(Appearance(match_id=1004, player_id=42, player_name="Kalle"))
        db.flush()

        m = make_match_dict(1004, goals_home=1, goals_away=0,
                            final_result_ts="2020-01-15T21:00:00")
        client = build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]))

        run_sync(db, client)

        client.fetch_lineups.assert_not_called()

    def test_spelad_utan_appearances_hamtar_lineups(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        # Match finns i DB men utan appearances
        db.add(Match(match_id=1005, team="A", competition_id=100,
                     kickoff=datetime(2020, 1, 15, 19), status="played", raw={}))
        db.flush()

        m = make_match_dict(1005, goals_home=2, goals_away=1,
                            final_result_ts="2020-01-15T21:00:00")
        lineups = make_lineups_dict(1005, away_players=[make_player_dict(99)])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={1005: lineups},
        )

        run_sync(db, client)

        client.fetch_lineups.assert_called_once_with(1005)

    def test_abandoned_utan_resultat_loggar_varning(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = make_match_dict(1006, abandoned=True)
        client = build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]))

        log = run_sync(db, client)

        assert log.ok is True
        assert any("avbruten" in w for w in log.warnings)
        client.fetch_lineups.assert_not_called()

    def test_abandoned_med_resultat_behandlas_normalt(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = make_match_dict(1007, abandoned=True, goals_home=1, goals_away=0,
                            final_result_ts="2020-01-15T20:30:00")
        lineups = make_lineups_dict(1007, away_players=[make_player_dict(77)])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={1007: lineups},
        )

        log = run_sync(db, client)

        assert log.ok is True
        assert not any("avbruten" in w for w in log.warnings)
        client.fetch_lineups.assert_called_once_with(1007)

    def test_b_match_sparas_med_team_b(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = make_match_dict(2001, away_team_id=TEAM_B_ID,
                            match_datetime="2099-01-01T19:00:00")
        client = build_client(team_b_dict=make_team_dict(TEAM_B_ID, [m]))

        run_sync(db, client)

        match = db.get(Match, 2001)
        assert match is not None
        assert match.team == "B"

    def test_spelare_uppdateras_vid_ny_match(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        db.add(Player(player_id=55, name="Gammalt Namn", shirt_no="10",
                      last_seen=datetime(2019, 1, 1)))
        db.flush()

        m = make_match_dict(1008, goals_home=1, goals_away=0,
                            final_result_ts="2020-01-15T21:00:00")
        lineups = make_lineups_dict(1008, away_players=[
            make_player_dict(55, "Nytt Namn", "11")
        ])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={1008: lineups},
        )

        run_sync(db, client)

        player = db.get(Player, 55)
        assert player.name == "Nytt Namn"
        assert player.shirt_no == "11"

    def test_sync_log_sparas_med_timestamp(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        client = build_client()
        result = run_sync(db, client)

        assert result.log_id is not None
        assert result.started_at is not None
        assert result.finished_at is not None
        assert result.finished_at >= result.started_at

        db_log = db.get(SyncLog, result.log_id)
        assert db_log is not None
        assert db_log.ok is True

    def test_nätverksfel_ger_ok_false_i_log(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        client = MagicMock(spec=IBISClient)
        client.fetch_team_raw.side_effect = ConnectionError("Nätverksfel")

        result = run_sync(db, client)

        assert result.ok is False
        assert any("Synken avbröts" in w for w in result.warnings)
        db_log = db.get(SyncLog, result.log_id)
        assert db_log.ok is False

    def test_motstandare_sätts_for_bortalag(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        # Tungelsta A är bortalag: HomeTeam är motståndaren
        m = make_match_dict(3001, home_team_id=OTHER_ID, away_team_id=TEAM_A_ID,
                            match_datetime="2099-01-01T19:00:00")
        client = build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]))

        run_sync(db, client)

        match = db.get(Match, 3001)
        assert match.opponent == f"Hemmalag {OTHER_ID}"

    def _non_series_team_dict(self, comp_type: int, match_dicts: list[dict]) -> dict:
        return {
            "TeamID": TEAM_A_ID,
            "Name": "Tungelsta IF",
            "Competitions": [
                {
                    "CompetitionID": 200,
                    "CompetitionTypeID": comp_type,
                    "Name": "Cupen" if comp_type == 3 else "Träningsmatcher",
                    "Matches": match_dicts,
                }
            ],
        }

    def test_cupmatch_far_lineups_men_raknas_inte(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        team_dict = self._non_series_team_dict(
            3, [make_match_dict(9001, final_result_ts="2020-01-15T21:00:00")]
        )
        lineups = make_lineups_dict(9001, away_players=[make_player_dict(42, "Kalle", "7")])
        client = build_client(team_a_dict=team_dict, lineups_by_id={9001: lineups})

        result = run_sync(db, client)

        match = db.get(Match, 9001)
        assert match is not None
        assert match.team == "A"
        assert match.counts_for_rules is False
        # Lineups hämtas och appearances sparas – de påverkar ändå inte
        # regelmotorn, som filtrerar bort matchen via counts_for_rules.
        client.fetch_lineups.assert_called_once_with(9001)
        apps = db.scalars(select(Appearance).where(Appearance.match_id == 9001)).all()
        assert len(apps) == 1
        assert apps[0].player_id == 42
        assert result.matches_added == 1

    def test_traningsmatch_annan_competitiontype_far_publicerad_trupp(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        # Konkret fall ur buggrapporten: match 1767137, träningsmatch mot
        # Hammarby, CompetitionTypeID 3, ej spelad än – men truppen är redan
        # publicerad i iBIS och ska sparas så registreringsvyn kan visa den.
        m = make_match_dict(1767137, home_team_id=TEAM_A_ID, away_team_id=OTHER_ID,
                            match_datetime="2026-09-01T20:20:00")
        lineups = make_lineups_dict(1767137, home_id=TEAM_A_ID, away_id=OTHER_ID,
                                    home_players=[make_player_dict(42, "Kalle", "7")])
        client = build_client(
            team_a_dict=self._non_series_team_dict(3, [m]),
            lineups_by_id={1767137: lineups},
        )

        run_sync(db, client)

        match = db.get(Match, 1767137)
        assert match is not None
        assert match.counts_for_rules is False
        client.fetch_lineups.assert_called_once_with(1767137)
        apps = db.scalars(select(Appearance).where(Appearance.match_id == 1767137)).all()
        assert len(apps) == 1
        assert apps[0].player_id == 42

    def test_traningsmatch_utan_publicerad_trupp_sparar_inget(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        # Samma träningsmatch, men truppen är ännu inte publicerad i iBIS
        # (tomma lineups) – då ska ingenting sparas.
        m = make_match_dict(1767137, home_team_id=TEAM_A_ID, away_team_id=OTHER_ID,
                            match_datetime="2026-09-01T20:20:00")
        client = build_client(team_a_dict=self._non_series_team_dict(3, [m]))

        run_sync(db, client)

        client.fetch_lineups.assert_called_once_with(1767137)
        assert db.scalars(
            select(Appearance).where(Appearance.match_id == 1767137)
        ).all() == []

    def test_seriematch_far_counts_for_rules_true(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = make_match_dict(9100, match_datetime="2099-01-01T19:00:00")
        client = build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]))

        run_sync(db, client)

        assert db.get(Match, 9100).counts_for_rules is True

    def test_matches_added_räknas_korrekt(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        matches_a = [
            make_match_dict(4001, match_datetime="2099-01-01T19:00:00"),
            make_match_dict(4002, match_datetime="2099-02-01T19:00:00"),
        ]
        matches_b = [make_match_dict(4003, away_team_id=TEAM_B_ID,
                                     match_datetime="2099-01-01T19:00:00")]
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, matches_a),
            team_b_dict=make_team_dict(TEAM_B_ID, matches_b),
        )

        result = run_sync(db, client)

        assert result.matches_added == 3

    def test_result_laesbart_efter_stangd_session(self, monkeypatch):
        """Regression: SyncLog-ORM lämnade sessionen → DetachedInstanceError."""
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        eng = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(eng)
        ClosingSession = sessionmaker(bind=eng)

        client = build_client()
        with ClosingSession() as session:
            result = run_sync(session, client)

        # Sessionen är nu stängd – dessa ska inte kasta DetachedInstanceError
        assert isinstance(result, SyncResult)
        assert result.ok is True
        assert result.matches_added == 0
        assert result.warnings == []
        assert result.started_at is not None
        assert result.finished_at is not None
        assert result.log_id is not None

    def test_trupp_spelare_fran_lagobjektet_sparas_for_bada_lag(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        squad_a = [make_squad_player_dict(101, "Spelare A-lag", 5)]
        squad_b = [make_squad_player_dict(202, "Spelare B-lag", 9)]

        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [], players=squad_a),
            team_b_dict=make_team_dict(TEAM_B_ID, [], players=squad_b),
        )

        log = run_sync(db, client)

        assert log.ok is True

        p_a = db.get(Player, 101)
        assert p_a is not None
        assert p_a.name == "Spelare A-lag"
        assert p_a.shirt_no == "5"

        p_b = db.get(Player, 202)
        assert p_b is not None
        assert p_b.name == "Spelare B-lag"
        assert p_b.shirt_no == "9"

    def test_spelare_i_bada_lagen_ger_en_rad_med_trojnummer(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        # Spelare 77 finns i A-lagets trupp med tröjnummer och i B-lagets utan
        squad_a = [make_squad_player_dict(77, "Pendel Spelare", 9)]
        squad_b = [make_squad_player_dict(77, "Pendel Spelare", None)]

        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [], players=squad_a),
            team_b_dict=make_team_dict(TEAM_B_ID, [], players=squad_b),
        )

        log = run_sync(db, client)

        assert log.ok is True
        player = db.get(Player, 77)
        assert player is not None
        assert player.shirt_no == "9"


# ---------------------------------------------------------------------------
# player_teams: lagtillhörighet som unionen av trupp-lista och appearances
# ---------------------------------------------------------------------------

class TestPlayerTeams:
    def _teams(self, db, player_id: int) -> set[str]:
        return set(db.scalars(
            select(PlayerTeam.team).where(PlayerTeam.player_id == player_id)
        ).all())

    def test_truppspelare_far_lagtillhorighet(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [], players=[
                make_squad_player_dict(101, "A-trupp", 5),
            ]),
        )
        assert run_sync(db, client).ok is True

        assert self._teams(db, 101) == {"A"}

    def test_appearance_ger_lagtillhorighet_utan_truppista(self, db, monkeypatch):
        """Andra källan: spelat för laget utan att stå i dess Players[]."""
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = make_match_dict(2100, away_team_id=TEAM_B_ID, goals_home=1, goals_away=2,
                            final_result_ts="2020-01-15T21:00:00")
        lineups = make_lineups_dict(2100, home_id=OTHER_ID, away_id=TEAM_B_ID,
                                    away_players=[make_player_dict(60, "Inhoppare", "8")])
        client = build_client(
            team_b_dict=make_team_dict(TEAM_B_ID, [m], players=[]),
            lineups_by_id={2100: lineups},
        )
        assert run_sync(db, client).ok is True

        assert self._teams(db, 60) == {"B"}

    def test_spelare_i_bada_lagen_far_bada_lagtillhorigheter(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        # Står i A:s trupp, men har bara spelat B-match
        b_match = make_match_dict(2200, away_team_id=TEAM_B_ID, goals_home=0, goals_away=1,
                                  final_result_ts="2020-02-01T21:00:00")
        b_lineups = make_lineups_dict(2200, home_id=OTHER_ID, away_id=TEAM_B_ID,
                                      away_players=[make_player_dict(77, "Pendlare", "9")])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [], players=[
                make_squad_player_dict(77, "Pendlare", 9),
            ]),
            team_b_dict=make_team_dict(TEAM_B_ID, [b_match], players=[]),
            lineups_by_id={2200: b_lineups},
        )
        assert run_sync(db, client).ok is True

        assert self._teams(db, 77) == {"A", "B"}

    def test_lagtillhorighet_ar_idempotent(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [], players=[
                make_squad_player_dict(101, "A-trupp", 5),
            ]),
        )
        run_sync(db, client)
        run_sync(db, client)

        rows = db.scalars(
            select(PlayerTeam).where(PlayerTeam.player_id == 101)
        ).all()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Steg 11: Goals/Assists/PenaltyMinutes på appearances + målvaktsmarkering
# ---------------------------------------------------------------------------

class TestAppearanceStats:
    def _played_match(self, match_id: int) -> dict:
        return make_match_dict(match_id, goals_home=3, goals_away=2,
                               final_result_ts="2020-01-15T21:00:00")

    def test_ny_appearance_far_statistik_fran_events(self, db, monkeypatch):
        """
        Lineups har inte längre Goals/Assists/PenaltyMinutes, så siffrorna
        räknas ur matchens Events: Kalle gör två mål, lägger fram till Lisas
        mål och sitter av två minuter.
        """
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = self._played_match(5001)
        lineups = make_lineups_dict(5001, away_players=[
            make_player_dict(42, "Kalle", "7"),
            make_player_dict(43, "Lisa", "8"),
        ])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={5001: lineups},
            events_by_id={5001: [
                make_event_dict(1, 1, 1, 42),
                make_event_dict(2, 1, 2, 42),
                make_event_dict(3, 1, 3, 43, assist_id=42),
                make_event_dict(4, 2, 2, 42, penalty_name="Slag, 2 min"),
            ]},
        )

        assert run_sync(db, client).ok is True

        app = db.get(Appearance, (5001, 42))
        assert app.goals == 2
        assert app.assists == 1
        assert app.penalty_minutes == 2
        assert db.get(Appearance, (5001, 43)).goals == 1

    def test_saknad_statistik_blir_noll(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = self._played_match(5002)
        lineups = make_lineups_dict(5002, away_players=[make_player_dict(43)])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={5002: lineups},
        )

        assert run_sync(db, client).ok is True

        app = db.get(Appearance, (5002, 43))
        assert app.goals == 0
        assert app.assists == 0
        assert app.penalty_minutes == 0

    def test_befintlig_appearance_uppdateras_vid_nasta_synk(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        # Match spelad men ännu inte färdigrapporterad, med en gammal
        # appearance utan statistik.
        db.add(Match(match_id=5003, team="A", competition_id=100,
                     kickoff=datetime(2020, 1, 15, 19), status="played", raw={}))
        db.add(Player(player_id=44, name="Kalle", last_seen=datetime(2020, 1, 15, 19)))
        db.add(Appearance(match_id=5003, player_id=44, player_name="Kalle"))
        db.flush()

        m = make_match_dict(5003, goals_home=2, goals_away=1)  # inget final_result_ts
        lineups = make_lineups_dict(5003, away_players=[
            make_player_dict(44, "Kalle", "7"),
            make_player_dict(47, "Lisa", "8"),
        ])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={5003: lineups},
            events_by_id={5003: [
                make_event_dict(1, 1, 1, 44),
                make_event_dict(2, 1, 2, 47, assist_id=44),
                make_event_dict(3, 1, 3, 47, assist_id=44),
            ]},
        )

        assert run_sync(db, client).ok is True

        client.fetch_lineups.assert_called_once_with(5003)
        app = db.get(Appearance, (5003, 44))
        assert app.goals == 1
        assert app.assists == 2

    def test_opublicerade_events_nollar_inte_befintlig_statistik(self, db, monkeypatch):
        """
        Events är enda källan till statistiken, och är null tills iBIS
        publicerat händelserna. Då ska tidigare sparade siffror stå kvar –
        att skriva nollor över riktiga mål vore sämre än att vänta.
        """
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        db.add(Match(match_id=5006, team="A", competition_id=100,
                     kickoff=datetime(2020, 1, 15, 19), status="played", raw={},
                     stats_final_ts=None))
        db.add(Player(player_id=48, name="Kalle", last_seen=datetime(2020, 1, 15, 19)))
        db.add(Appearance(match_id=5006, player_id=48, player_name="Kalle",
                          goals=3, assists=1, penalty_minutes=2))
        db.flush()

        m = self._played_match(5006)
        lineups = make_lineups_dict(5006, away_players=[
            make_player_dict(48, "Kalle", "7"),
        ])
        # Inga events_by_id: fetch_match_raw svarar med Events = null.
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={5006: lineups},
        )

        assert run_sync(db, client).ok is True

        app = db.get(Appearance, (5006, 48))
        assert app.goals == 3
        assert app.assists == 1
        assert app.penalty_minutes == 2

    def test_fardigrapporterad_match_med_appearances_hamtas_inte_om(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        db.add(Match(match_id=5004, team="A", competition_id=100,
                     kickoff=datetime(2020, 1, 15, 19), status="played", raw={},
                     stats_final_ts="2020-01-15T21:00:00"))
        db.add(Player(player_id=45, name="Kalle", last_seen=datetime(2020, 1, 15, 19)))
        db.add(Appearance(match_id=5004, player_id=45, player_name="Kalle", goals=1))
        db.flush()

        m = self._played_match(5004)
        client = build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]))

        assert run_sync(db, client).ok is True
        client.fetch_lineups.assert_not_called()

    def test_statistik_hamtad_mitt_i_matchen_hamtas_om_efter_slutrapport(
        self, db, monkeypatch
    ):
        """
        Trupper publiceras före matchstart, så appearances skrivs redan under
        matchens gång – och är då halvfärdiga. En spelare som får sin andra
        utvisning i tredje perioden hann sparas med 2 minuter i stället för 4.

        Matchen måste därför hämtas om en gång efter slutrapporten. Det är
        skillnaden mot testet ovan: där hämtades statistiken redan efter
        slutrapporten, här mitt under matchen (stats_final_ts är null).
        """
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        db.add(Match(match_id=5005, team="A", competition_id=100,
                     kickoff=datetime(2020, 1, 15, 19), status="played", raw={},
                     stats_final_ts=None))
        db.add(Player(player_id=46, name="Joacim", last_seen=datetime(2020, 1, 15, 19)))
        db.add(Appearance(match_id=5005, player_id=46, player_name="Joacim",
                          penalty_minutes=2))
        db.flush()

        m = self._played_match(5005)
        lineups = make_lineups_dict(5005, away_players=[
            make_player_dict(46, "Joacim"),
        ])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={5005: lineups},
            events_by_id={5005: [
                make_event_dict(1, 2, 1, 46, penalty_name="Slag, 2 min"),
                make_event_dict(2, 2, 3, 46, penalty_name="Hårt spel 2 min"),
            ]},
        )

        assert run_sync(db, client).ok is True

        client.fetch_lineups.assert_called_once_with(5005)
        assert db.get(Appearance, (5005, 46)).penalty_minutes == 4
        # Stämpeln sätts, så nästa synk hoppar över matchen.
        assert db.get(Match, 5005).stats_final_ts == "2020-01-15T21:00:00"


class TestGoalkeeperFlag:
    def _played_match(self, match_id: int) -> dict:
        return make_match_dict(match_id, goals_home=3, goals_away=2,
                               final_result_ts="2020-01-15T21:00:00")

    def test_position_malvakt_ger_is_goalkeeper(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = self._played_match(6001)
        lineups = make_lineups_dict(6001, away_players=[
            make_player_dict(50, "MV Svensson", "1", position="Målvakt"),
        ])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={6001: lineups},
        )

        assert run_sync(db, client).ok is True
        assert db.get(Player, 50).is_goalkeeper is True

    def test_saknad_position_ger_false(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = self._played_match(6002)
        lineups = make_lineups_dict(6002, away_players=[make_player_dict(51)])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={6002: lineups},
        )

        assert run_sync(db, client).ok is True
        assert db.get(Player, 51).is_goalkeeper is False

    def test_tom_position_nollar_inte_tidigare_markering(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        db.add(Player(player_id=52, name="MV Svensson", is_goalkeeper=True,
                      last_seen=datetime(2019, 1, 1)))
        db.flush()

        m = self._played_match(6003)
        lineups = make_lineups_dict(6003, away_players=[
            make_player_dict(52, "MV Svensson", "1"),  # ingen position
        ])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={6003: lineups},
        )

        assert run_sync(db, client).ok is True
        assert db.get(Player, 52).is_goalkeeper is True


# ---------------------------------------------------------------------------
# Synken robust mot enskilda fel (SPEC 3.5)
#
# Ett valideringsfel på EN match ska loggas som varning i sync_log och matchen
# hoppas över – resten av synken fortsätter.
# ---------------------------------------------------------------------------

class TestSyncRobusthet:
    def test_ogiltig_lineup_pa_en_match_avbryter_inte_synken(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        bad = make_match_dict(7001, goals_home=1, goals_away=0,
                              final_result_ts="2020-01-15T21:00:00")
        good = make_match_dict(7002, goals_home=2, goals_away=1,
                               final_result_ts="2020-01-15T21:00:00")
        good_lineups = make_lineups_dict(
            7002, away_players=[make_player_dict(50, "Ok", "3")]
        )

        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [bad, good]),
            lineups_by_id={7002: good_lineups},
        )

        def fetch_lineups(match_id):
            if match_id == 7001:
                # iBIS returnerar ogiltig data: PlayerID går inte att tolka som
                # tal → pydantic-valideringsfel, precis som ShirtNo-buggen.
                return IBISLineups.model_validate({
                    "MatchID": 7001, "HomeTeamID": OTHER_ID, "AwayTeamID": TEAM_A_ID,
                    "HomeTeamPlayers": [],
                    "AwayTeamPlayers": [
                        {"PlayerID": "inte-ett-tal", "MatchPlayerID": 1, "Name": "X"},
                    ],
                })
            return IBISLineups.model_validate(good_lineups)

        client.fetch_lineups.side_effect = fetch_lineups

        result = run_sync(db, client)

        # Synken avbröts inte
        assert result.ok is True
        # Varningen om den trasiga matchen hamnade i sync_log
        assert any("7001" in w for w in result.warnings)
        db_log = db.get(SyncLog, result.log_id)
        assert db_log.ok is True
        assert any("7001" in w for w in db_log.warnings)
        # Resten av synken fortsatte: den friska matchen fick sina appearances
        apps = db.scalars(select(Appearance).where(Appearance.match_id == 7002)).all()
        assert len(apps) == 1
        assert apps[0].player_id == 50
        # Båda matchraderna sparades ändå så de syns i matchlistan
        assert db.get(Match, 7001) is not None
        assert db.get(Match, 7002) is not None

    def test_shirtno_som_int_i_lineups_sparas_som_str(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = make_match_dict(7100, goals_home=1, goals_away=0,
                            final_result_ts="2020-01-15T21:00:00")
        # ShirtNo som int – tidigare kraschade IBISLineups-valideringen här
        lineups = make_lineups_dict(7100, away_players=[make_player_dict(60, "Kalle", 7)])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={7100: lineups},
        )

        assert run_sync(db, client).ok is True

        app = db.get(Appearance, (7100, 60))
        assert app is not None
        assert app.shirt_no == "7"


# ---------------------------------------------------------------------------
# Synken uppdaterar befintliga matchrader (buggrapport punkt 1)
#
# Synken lade tidigare bara till nya matcher. En match som flyttats i iBIS
# (nytt MatchDateTime) ska få nytt kickoff, ny status, nytt resultat, ny
# motståndare, ny hall och ny omgång vid nästa synk – även när lineups hoppas
# över. Registrerade skott hör till match_id och ska följa med.
# ---------------------------------------------------------------------------

def _played(match_id: int, when: str, **kw) -> dict:
    """Ett spelat match-dict med resultat och färdigrapportering samma dag."""
    return make_match_dict(
        match_id, match_datetime=when, goals_home=3, goals_away=2,
        final_result_ts=when[:11] + "21:00:00", **kw,
    )


class TestBefintligMatchUppdateras:
    def test_andrad_matchdatetime_ger_nytt_kickoff_efter_synk(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        # Match finns redan med gammalt datum och gammal omgång/motståndare.
        db.add(Match(
            match_id=1001, team="A", competition_id=100,
            kickoff=datetime(2026, 9, 19, 13, 15), status="scheduled",
            round_name="Omgång 5", opponent="Gammalt lag", raw={},
        ))
        db.flush()

        # iBIS har flyttat matchen till 14 september 20:00 (konkret fall i
        # buggrapporten: IFK Haninge).
        m = make_match_dict(
            1001, home_team_id=OTHER_ID, away_team_id=TEAM_A_ID,
            match_datetime="2026-09-14T20:00:00", round_name="Omgång 3",
        )
        client = build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]))

        result = run_sync(db, client)

        assert result.ok is True
        # Ingen ny match lades till – den befintliga uppdaterades.
        assert result.matches_added == 0

        match = db.get(Match, 1001)
        assert match.kickoff == datetime(2026, 9, 14, 20, 0)
        assert match.round_name == "Omgång 3"
        assert match.opponent == f"Hemmalag {OTHER_ID}"
        assert match.raw["MatchDateTime"] == "2026-09-14T20:00:00"

    def test_fardigrapporterad_match_far_rattat_resultat(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        db.add(Match(
            match_id=1002, team="A", competition_id=100,
            kickoff=datetime(2026, 8, 1, 19), status="played", raw={
                "GoalsHomeTeam": 2, "GoalsAwayTeam": 2,
                "FinalResultCreatedTS": "2026-08-01T21:00:00",
            },
            stats_final_ts="2026-08-01T21:00:00",
        ))
        db.add(Player(player_id=42, name="Kalle", last_seen=datetime(2026, 8, 1)))
        db.add(Appearance(match_id=1002, player_id=42, player_name="Kalle"))
        db.flush()

        # Sekretariatet rättar 2-2 till 3-2 i efterhand.
        m = make_match_dict(
            1002, match_datetime="2026-08-01T19:00:00",
            goals_home=3, goals_away=2,
            final_result_ts="2026-08-01T21:00:00",
        )
        client = build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]))

        assert run_sync(db, client).ok is True

        match = db.get(Match, 1002)
        assert match.raw["GoalsHomeTeam"] == 3
        # Lineups hämtas inte om för en färdigrapporterad match med appearances…
        client.fetch_lineups.assert_not_called()
        # …men matchraden uppdateras ändå.

    def test_skott_finns_kvar_efter_datumandring(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        db.add(Match(
            match_id=1003, team="A", competition_id=100,
            kickoff=datetime(2026, 9, 19, 13, 15), status="scheduled", raw={},
        ))
        db.add(Player(player_id=7, name="Skytt", last_seen=datetime(2026, 9, 1)))
        db.add(ShotEvent(
            id="uuid-1", match_id=1003, player_id=7, side="egen",
            kind="on_goal", period=1, created_at=datetime(2026, 9, 19, 13, 30),
        ))
        db.add(ShotEvent(
            id="uuid-2", match_id=1003, player_id=None, side="motstandare",
            kind="missed", period=2, created_at=datetime(2026, 9, 19, 14, 0),
        ))
        db.flush()

        m = make_match_dict(1003, home_team_id=TEAM_A_ID, away_team_id=OTHER_ID,
                            match_datetime="2026-09-14T20:00:00")
        client = build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]))

        assert run_sync(db, client).ok is True

        match = db.get(Match, 1003)
        assert match.kickoff == datetime(2026, 9, 14, 20, 0)
        shots = db.scalars(
            select(ShotEvent).where(ShotEvent.match_id == 1003)
        ).all()
        assert {s.id for s in shots} == {"uuid-1", "uuid-2"}

    def test_b_match_flyttad_fore_a_match_paverkar_kedjan(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        # Spelare 42 står i en A-match (10 aug) och en B-match (20 aug). Med den
        # ordningen spelade han A innan någon B → låst.
        a_match = _played(100, "2026-08-10T19:00:00",
                          home_team_id=OTHER_ID, away_team_id=TEAM_A_ID)
        b_match_late = _played(200, "2026-08-20T19:00:00",
                               home_team_id=OTHER_ID, away_team_id=TEAM_B_ID)
        lineups = {
            100: make_lineups_dict(100, OTHER_ID, TEAM_A_ID,
                                   away_players=[make_player_dict(42, "Pelle")]),
            200: make_lineups_dict(200, OTHER_ID, TEAM_B_ID,
                                   away_players=[make_player_dict(42, "Pelle")]),
        }
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [a_match]),
            team_b_dict=make_team_dict(TEAM_B_ID, [b_match_late]),
            lineups_by_id=lineups,
        )
        assert run_sync(db, client).ok is True

        statuses, _ = get_statuses(db)
        assert statuses[42].locked is True

        # iBIS flyttar B-matchen till 5 augusti – nu ligger den före A-matchen.
        b_match_early = _played(200, "2026-08-05T19:00:00",
                                home_team_id=OTHER_ID, away_team_id=TEAM_B_ID)
        client2 = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [a_match]),
            team_b_dict=make_team_dict(TEAM_B_ID, [b_match_early]),
            lineups_by_id=lineups,
        )
        assert run_sync(db, client2).ok is True

        assert db.get(Match, 200).kickoff == datetime(2026, 8, 5, 19, 0)
        statuses2, _ = get_statuses(db)
        assert statuses2[42].locked is False
        assert statuses2[42].has_b_appearance is True
        assert statuses2[42].matches_left == 1


# ---------------------------------------------------------------------------
# Matcher utan satt datum (buggrapport punkt 2)
# ---------------------------------------------------------------------------

class TestMatchUtanSattDatum:
    def test_platshallardatum_1_januari_markeras(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = make_match_dict(3001, match_datetime="2026-01-01T00:00:00")
        client = build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]))

        assert run_sync(db, client).ok is True
        assert db.get(Match, 3001).date_missing is True

    def test_match_time_missing_flagga_markeras(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        # Riktigt utseende på datumet, men MatchTimeMissing är satt.
        m = make_match_dict(3002, match_datetime="2026-10-01T19:00:00",
                            match_time_missing=True)
        client = build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]))

        assert run_sync(db, client).ok is True
        assert db.get(Match, 3002).date_missing is True

    def test_riktigt_datum_ger_date_missing_false(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = make_match_dict(3003, match_datetime="2026-09-14T20:00:00")
        client = build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]))

        assert run_sync(db, client).ok is True
        assert db.get(Match, 3003).date_missing is False

    def test_match_utan_datum_paverkar_aldrig_kedja_eller_lasstatus(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        # A-match daterad 1 januari (har passerat) med registrerat resultat och
        # spelare 42 i truppen, utan någon B-match först. Räknades den skulle
        # kvalificeringsregeln låsa honom direkt.
        m = make_match_dict(3100, home_team_id=OTHER_ID, away_team_id=TEAM_A_ID,
                            match_datetime="2026-01-01T00:00:00",
                            goals_home=1, goals_away=0,
                            final_result_ts="2026-01-01T21:00:00")
        lineups = make_lineups_dict(3100, OTHER_ID, TEAM_A_ID,
                                    away_players=[make_player_dict(42, "Pelle")])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={3100: lineups},
        )
        assert run_sync(db, client).ok is True

        match = db.get(Match, 3100)
        assert match.date_missing is True
        statuses, _ = get_statuses(db)
        assert 42 not in statuses

    def test_match_flyttar_ratt_nar_riktigt_datum_kommer_in(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        # Först utan datum: markeras och hålls utanför regelmotorn.
        placeholder = make_match_dict(
            3200, home_team_id=OTHER_ID, away_team_id=TEAM_A_ID,
            match_datetime="2026-01-01T00:00:00",
        )
        lineups = make_lineups_dict(3200, OTHER_ID, TEAM_A_ID,
                                    away_players=[make_player_dict(42, "Pelle")])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [placeholder]),
            lineups_by_id={3200: lineups},
        )
        assert run_sync(db, client).ok is True
        assert db.get(Match, 3200).date_missing is True
        assert 42 not in get_statuses(db)[0]

        # iBIS får ett riktigt datum och matchen spelas.
        real = make_match_dict(
            3200, home_team_id=OTHER_ID, away_team_id=TEAM_A_ID,
            match_datetime="2026-09-14T20:00:00",
            goals_home=1, goals_away=0,
            final_result_ts="2026-09-14T21:30:00",
        )
        client2 = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [real]),
            lineups_by_id={3200: lineups},
        )
        assert run_sync(db, client2).ok is True

        match = db.get(Match, 3200)
        assert match.date_missing is False
        assert match.kickoff == datetime(2026, 9, 14, 20, 0)
        # Nu räknas matchen: spelare 42 spelade A utan B först → låst.
        statuses, _ = get_statuses(db)
        assert statuses[42].locked is True


class TestBorttagenUrMatchtruppen:
    """
    En spelare som plockas ur matchtruppen i iBIS ska försvinna ur appearances
    vid nästa synk. Synken speglade tidigare bara tillägg, så spelaren låg kvar
    och fick tas bort för hand i Ändra matchlista.

    Konkret fall ur buggrapporten: William Lindahl stod i truppen för
    Täbymatchen (1703100) när den hämtades, men togs sedan bort i iBIS.
    """

    def _spelad(self, match_id: int = 1703100) -> dict:
        return make_match_dict(
            match_id, match_datetime="2026-09-19T13:15:00",
            goals_home=2, goals_away=3,
            final_result_ts="2026-09-19T15:30:00",
        )

    def test_spelare_borta_ur_ibis_tas_bort_vid_nasta_synk(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = self._spelad()
        full = make_lineups_dict(1703100, away_players=[
            make_player_dict(42, "Kalle", "7"),
            make_player_dict(77, "William Lindahl", "22"),
        ])
        assert run_sync(
            db,
            build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]),
                         lineups_by_id={1703100: full}),
        ).ok is True
        assert {a.player_id for a in db.scalars(
            select(Appearance).where(Appearance.match_id == 1703100)
        ).all()} == {42, 77}

        # iBIS plockar bort 77 ur truppen. Matchen hämtades om ovan och skulle
        # nu hoppas över, så nolla stämpeln – samma läge som när iBIS rättar
        # slutrapporten och matchen hämtas om (SPEC 6.7).
        db.get(Match, 1703100).stats_final_ts = None
        db.flush()

        utan = make_lineups_dict(1703100, away_players=[
            make_player_dict(42, "Kalle", "7"),
        ])
        log = run_sync(
            db,
            build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]),
                         lineups_by_id={1703100: utan}),
        )

        assert log.ok is True
        assert {a.player_id for a in db.scalars(
            select(Appearance).where(Appearance.match_id == 1703100)
        ).all()} == {42}
        assert any("borttagen ur matchtruppen" in w for w in log.warnings)

    def test_borttagen_spelare_forsvinner_ur_kedjan(self, db, monkeypatch):
        """Regelmotorn räknar på appearances – borttagningen ska slå igenom."""
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        def spelad(match_id, datum, away_id=TEAM_A_ID):
            return make_match_dict(
                match_id, away_team_id=away_id, match_datetime=f"{datum}T19:00:00",
                goals_home=1, goals_away=0,
                final_result_ts=f"{datum}T21:00:00",
            )

        # B-match först, så kvalificeringsregeln är uppfylld och det bara är
        # kedjeregeln som avgör. Tre A-matcher i rad låser på den tredje.
        b0 = spelad(2000, "2026-08-25", away_id=TEAM_B_ID)
        a1 = spelad(2001, "2026-09-01")
        a2 = spelad(2002, "2026-09-08")
        a3 = spelad(2003, "2026-09-15")
        kalle = [make_player_dict(42, "Kalle", "7")]
        lineups = {
            mid: make_lineups_dict(mid, away_players=kalle)
            for mid in (2000, 2001, 2002, 2003)
        }

        def kor():
            return run_sync(
                db,
                build_client(
                    team_a_dict=make_team_dict(TEAM_A_ID, [a1, a2, a3]),
                    team_b_dict=make_team_dict(TEAM_B_ID, [b0]),
                    lineups_by_id=lineups,
                ),
            )

        assert kor().ok is True
        assert get_statuses(db)[0][42].locked is True

        # iBIS plockar bort honom ur den tredje matchen. Truppen är inte tom –
        # en annan spelare står kvar.
        db.get(Match, 2003).stats_final_ts = None
        db.flush()
        lineups[2003] = make_lineups_dict(2003, away_players=[
            make_player_dict(99, "Annan", "8"),
        ])

        assert kor().ok is True

        assert db.get(Appearance, (2003, 42)) is None
        # Kvar: två A-matcher. Kedjeregeln låser först på den tredje.
        assert get_statuses(db)[0][42].locked is False

    def test_tom_lineup_tar_inte_bort_nagot(self, db, monkeypatch):
        """Opublicerad trupp betyder inte att alla plockats ur (tom lista)."""
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = self._spelad(1703101)
        full = make_lineups_dict(1703101, away_players=[
            make_player_dict(42, "Kalle", "7"),
        ])
        assert run_sync(
            db,
            build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]),
                         lineups_by_id={1703101: full}),
        ).ok is True

        db.get(Match, 1703101).stats_final_ts = None
        db.flush()

        tom = make_lineups_dict(1703101, away_players=[])
        log = run_sync(
            db,
            build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]),
                         lineups_by_id={1703101: tom}),
        )

        assert log.ok is True
        assert db.get(Appearance, (1703101, 42)) is not None
        assert not any("matchtruppen i iBIS" in w for w in log.warnings)

    def test_null_lineup_tar_inte_bort_nagot(self, db, monkeypatch):
        """Samma sak när iBIS svarar med null i stället för en tom lista."""
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = self._spelad(1703102)
        full = make_lineups_dict(1703102, away_players=[
            make_player_dict(42, "Kalle", "7"),
        ])
        assert run_sync(
            db,
            build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]),
                         lineups_by_id={1703102: full}),
        ).ok is True

        db.get(Match, 1703102).stats_final_ts = None
        db.flush()

        null_lineup = make_lineups_dict(1703102)
        null_lineup["HomeTeamPlayers"] = None
        null_lineup["AwayTeamPlayers"] = None
        log = run_sync(
            db,
            build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]),
                         lineups_by_id={1703102: null_lineup}),
        )

        assert log.ok is True
        assert db.get(Appearance, (1703102, 42)) is not None

    def test_manuellt_tillagd_spelare_finns_kvar_efter_synk(self, db, monkeypatch):
        """
        roster_edits är ett eget lager ovanpå iBIS (SPEC 6.5). En manuellt
        tillagd spelare har ingen appearance att ta bort, och ska finnas kvar i
        den effektiva truppen även när han aldrig stått i iBIS.
        """
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = self._spelad(1703103)
        lineups = make_lineups_dict(1703103, away_players=[
            make_player_dict(42, "Kalle", "7"),
        ])
        assert run_sync(
            db,
            build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]),
                         lineups_by_id={1703103: lineups}),
        ).ok is True

        db.add(Player(player_id=55, name="Manuell Spelare",
                      last_seen=datetime(2026, 9, 19, 13, 15)))
        db.add(RosterEdit(
            match_id=1703103, player_id=55, action="add",
            note="stod i truppen men saknas i iBIS",
            created_at=datetime(2026, 9, 19, 16, 0), created_by="TL",
        ))
        db.get(Match, 1703103).stats_final_ts = None
        db.flush()

        assert run_sync(
            db,
            build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]),
                         lineups_by_id={1703103: lineups}),
        ).ok is True

        # Editen är orörd och spelaren räknas fortfarande i regelmotorn.
        edits = db.scalars(
            select(RosterEdit).where(RosterEdit.match_id == 1703103)
        ).all()
        assert [(e.player_id, e.action) for e in edits] == [(55, "add")]
        assert 55 in get_statuses(db)[0]

    def test_overflodig_manuell_borttagning_kraschar_inte(self, db, monkeypatch):
        """
        iBIS hinner före en manuell borttagning: editen pekar nu på en spelare
        som inte längre har någon appearance. Det ska gå igenom utan fel.
        """
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = self._spelad(1703104)
        full = make_lineups_dict(1703104, away_players=[
            make_player_dict(42, "Kalle", "7"),
            make_player_dict(77, "William Lindahl", "22"),
        ])
        assert run_sync(
            db,
            build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]),
                         lineups_by_id={1703104: full}),
        ).ok is True

        # Tränaren tog bort honom för hand innan iBIS rättades.
        db.add(RosterEdit(
            match_id=1703104, player_id=77, action="remove",
            note="stod inte i truppen",
            created_at=datetime(2026, 9, 19, 16, 0), created_by="TL",
        ))
        db.get(Match, 1703104).stats_final_ts = None
        db.flush()

        utan = make_lineups_dict(1703104, away_players=[
            make_player_dict(42, "Kalle", "7"),
        ])
        log = run_sync(
            db,
            build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]),
                         lineups_by_id={1703104: utan}),
        )

        assert log.ok is True
        assert db.get(Appearance, (1703104, 77)) is None
        statuses, _ = get_statuses(db)
        assert 77 not in statuses

    def test_fardigrapporterad_match_ror_inte_appearances(self, db, monkeypatch):
        """
        En match vars statistik hämtades efter slutrapporten hämtas inte om
        (SPEC 3.5) – då får truppen inte heller röras.
        """
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        db.add(Match(match_id=1703105, team="A", competition_id=100,
                     kickoff=datetime(2026, 9, 19, 13, 15), status="played",
                     raw={}, stats_final_ts="2026-09-19T15:30:00"))
        db.add(Player(player_id=77, name="William Lindahl",
                      last_seen=datetime(2026, 9, 19, 13, 15)))
        db.add(Appearance(match_id=1703105, player_id=77,
                          player_name="William Lindahl", shirt_no="22"))
        db.flush()

        m = self._spelad(1703105)
        tom = make_lineups_dict(1703105, away_players=[])
        client = build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]),
                              lineups_by_id={1703105: tom})

        assert run_sync(db, client).ok is True

        client.fetch_lineups.assert_not_called()
        assert db.get(Appearance, (1703105, 77)) is not None

    def test_spelaren_finns_kvar_i_players_med_sin_historik(self, db, monkeypatch):
        """
        Bara appearance-raden för matchen försvinner. Spelaren, hans övriga
        matcher och hans registrerade skott är orörda.
        """
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        tidigare = make_match_dict(2101, match_datetime="2026-09-12T19:00:00",
                                   goals_home=1, goals_away=0,
                                   final_result_ts="2026-09-12T21:00:00")
        senare = self._spelad(2102)
        william = make_player_dict(77, "William Lindahl", "22")
        lineups = {
            2101: make_lineups_dict(2101, away_players=[william]),
            2102: make_lineups_dict(2102, away_players=[william]),
        }
        assert run_sync(
            db,
            build_client(team_a_dict=make_team_dict(TEAM_A_ID, [tidigare, senare]),
                         lineups_by_id=lineups),
        ).ok is True

        db.add(ShotEvent(
            id="uuid-william", match_id=2102, player_id=77, side="egen",
            kind="on_goal", period=2, created_at=datetime(2026, 9, 19, 14, 0),
        ))
        db.get(Match, 2102).stats_final_ts = None
        db.flush()

        lineups[2102] = make_lineups_dict(2102, away_players=[
            make_player_dict(42, "Kalle", "7"),
        ])
        assert run_sync(
            db,
            build_client(team_a_dict=make_team_dict(TEAM_A_ID, [tidigare, senare]),
                         lineups_by_id=lineups),
        ).ok is True

        spelare = db.get(Player, 77)
        assert spelare is not None
        assert spelare.name == "William Lindahl"
        assert spelare.shirt_no == "22"
        # Den tidigare matchen är orörd, den senare borta.
        assert db.get(Appearance, (2101, 77)) is not None
        assert db.get(Appearance, (2102, 77)) is None
        # Skotten ligger kvar i databasen – de raderas aldrig av en synk.
        assert db.get(ShotEvent, "uuid-william") is not None


class TestPruneAppearances:
    """Enhetstester för _prune_appearances."""

    def _bygg(self, db, match_id=3001, player_ids=(1, 2)):
        db.add(Match(match_id=match_id, team="A", competition_id=100,
                     kickoff=datetime(2026, 9, 19, 13, 15), status="played",
                     raw={}))
        for pid in player_ids:
            db.add(Player(player_id=pid, name=f"Spelare {pid}",
                          last_seen=datetime(2026, 9, 19, 13, 15)))
            db.add(Appearance(match_id=match_id, player_id=pid,
                              player_name=f"Spelare {pid}"))
        db.flush()

    def test_tar_bort_den_som_inte_star_i_truppen(self, db):
        self._bygg(db)
        assert _prune_appearances(db, 3001, {1}) == 1
        assert db.get(Appearance, (3001, 1)) is not None
        assert db.get(Appearance, (3001, 2)) is None

    def test_oforandrad_trupp_tar_inte_bort_nagot(self, db):
        self._bygg(db)
        assert _prune_appearances(db, 3001, {1, 2}) == 0

    def test_ror_bara_den_angivna_matchen(self, db):
        self._bygg(db, 3001, (1, 2))
        db.add(Match(match_id=3002, team="A", competition_id=100,
                     kickoff=datetime(2026, 9, 26, 13, 15), status="played",
                     raw={}))
        db.add(Appearance(match_id=3002, player_id=2, player_name="Spelare 2"))
        db.flush()

        assert _prune_appearances(db, 3001, {1}) == 1
        assert db.get(Appearance, (3002, 2)) is not None
