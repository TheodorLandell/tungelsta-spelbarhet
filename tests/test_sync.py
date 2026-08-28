"""
Tester för synkjobbet. Inga nätverksanrop – klienten är mockad.
Databasen är en in-memory SQLite-instans per test.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.ibis_client import IBISClient, IBISLineups, IBISTeam
from app.models import Appearance, Base, Match, Player, PlayerTeam, SyncLog
from app.sync import (
    SyncResult,
    _has_appearances,
    _match_status,
    _opponent,
    _upsert_match,
    _upsert_player,
    run_sync,
)

TEAM_A_ID = 1977
TEAM_B_ID = 17541
OTHER_ID = 9999

MOCK_SETTINGS = SimpleNamespace(season_id=44, team_a_id=TEAM_A_ID, team_b_id=TEAM_B_ID)


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


def build_client(
    team_a_dict: dict | None = None,
    team_b_dict: dict | None = None,
    lineups_by_id: dict[int, dict] | None = None,
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

    client.fetch_team_raw.side_effect = fetch_team_raw
    client.fetch_lineups.side_effect = fetch_lineups
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

    def test_schemalagd_match_ger_inga_appearances(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = make_match_dict(1002, match_datetime="2099-01-01T19:00:00")
        client = build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]))

        log = run_sync(db, client)

        assert log.ok is True
        match = db.get(Match, 1002)
        assert match.status == "scheduled"
        client.fetch_lineups.assert_not_called()

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

        # Förbered match + appearances i DB
        db.add(Match(match_id=1004, team="A", competition_id=100,
                     kickoff=datetime(2020, 1, 15, 19), status="played", raw={}))
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

    def test_cupmatchar_ignoreras(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        cup_comp = {
            "CompetitionID": 200,
            "CompetitionTypeID": 3,
            "Name": "Cupen",
            "Matches": [make_match_dict(9001, final_result_ts="2020-01-15T21:00:00")],
        }
        team_dict = {
            "TeamID": TEAM_A_ID,
            "Name": "Tungelsta IF",
            "Competitions": [cup_comp],
        }
        client = build_client(team_a_dict=team_dict)

        run_sync(db, client)

        assert db.get(Match, 9001) is None
        client.fetch_lineups.assert_not_called()

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

    def test_ny_appearance_far_statistik_fran_lineups(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        m = self._played_match(5001)
        lineups = make_lineups_dict(5001, away_players=[
            make_player_dict(42, "Kalle", "7", goals=2, assists=1, penalty_minutes=2),
        ])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={5001: lineups},
        )

        assert run_sync(db, client).ok is True

        app = db.get(Appearance, (5001, 42))
        assert app.goals == 2
        assert app.assists == 1
        assert app.penalty_minutes == 2

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
            make_player_dict(44, "Kalle", "7", goals=1, assists=2, penalty_minutes=0),
        ])
        client = build_client(
            team_a_dict=make_team_dict(TEAM_A_ID, [m]),
            lineups_by_id={5003: lineups},
        )

        assert run_sync(db, client).ok is True

        client.fetch_lineups.assert_called_once_with(5003)
        app = db.get(Appearance, (5003, 44))
        assert app.goals == 1
        assert app.assists == 2

    def test_fardigrapporterad_match_med_appearances_hamtas_inte_om(self, db, monkeypatch):
        monkeypatch.setattr("app.sync.settings", MOCK_SETTINGS)

        db.add(Match(match_id=5004, team="A", competition_id=100,
                     kickoff=datetime(2020, 1, 15, 19), status="played", raw={}))
        db.add(Player(player_id=45, name="Kalle", last_seen=datetime(2020, 1, 15, 19)))
        db.add(Appearance(match_id=5004, player_id=45, player_name="Kalle", goals=1))
        db.flush()

        m = self._played_match(5004)
        client = build_client(team_a_dict=make_team_dict(TEAM_A_ID, [m]))

        assert run_sync(db, client).ok is True
        client.fetch_lineups.assert_not_called()


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
