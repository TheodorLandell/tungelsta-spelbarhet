"""
Enhetstester för iBIS-klienten. Inga nätverksanrop – testar mot sparade fixtures.
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from app.ibis_client import (
    IBISCompetition,
    IBISLineups,
    IBISMatch,
    IBISMatchPlayer,
    IBISSquadPlayer,
    IBISTeam,
    filter_series_competitions,
    get_team_players,
    is_played,
    parse_kickoff,
)

FIXTURES = Path(__file__).parent / "fixtures"
STOCKHOLM = timezone(timedelta(hours=2))

TEAM_A_ID = 1977
TEAM_B_ID = 17541


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def team_a() -> IBISTeam:
    return IBISTeam.model_validate(json.loads((FIXTURES / "team_a.json").read_text(encoding="utf-8")))


@pytest.fixture
def team_b() -> IBISTeam:
    return IBISTeam.model_validate(json.loads((FIXTURES / "team_b.json").read_text(encoding="utf-8")))


@pytest.fixture
def lineups_played() -> IBISLineups:
    return IBISLineups.model_validate(json.loads((FIXTURES / "lineups_played.json").read_text(encoding="utf-8")))


@pytest.fixture
def lineups_scheduled() -> IBISLineups:
    return IBISLineups.model_validate(json.loads((FIXTURES / "lineups_scheduled.json").read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# parse_kickoff
# ---------------------------------------------------------------------------

class TestParseKickoff:
    def test_parsar_naiv_strang(self):
        dt = parse_kickoff("2026-09-19T13:00:00")
        assert dt.year == 2026
        assert dt.month == 9
        assert dt.day == 19
        assert dt.hour == 13
        assert dt.tzinfo is not None

    def test_timezone_ar_stockholm(self):
        dt = parse_kickoff("2026-09-19T13:00:00")
        assert dt.utcoffset() == timedelta(hours=2)


# ---------------------------------------------------------------------------
# IBISMatch-modellen
# ---------------------------------------------------------------------------

class TestIBISMatch:
    def _match(self, **overrides) -> IBISMatch:
        defaults = dict(
            MatchID=1,
            CompetitionID=100,
            CompetitionTypeID=1,
            HomeTeamID=4192,
            AwayTeamID=TEAM_A_ID,
            MatchDateTime="2026-09-19T13:00:00",
            Cancelled=False,
            Postponed=False,
            Abandoned=False,
        )
        return IBISMatch(**{**defaults, **overrides})

    def test_bool_falt_accepterar_bool(self):
        m = self._match(Cancelled=True)
        assert m.Cancelled is True

    def test_bool_falt_coercar_strang(self):
        m = IBISMatch.model_validate({
            "MatchID": 1, "CompetitionID": 100, "CompetitionTypeID": 1,
            "HomeTeamID": 1, "AwayTeamID": 2,
            "MatchDateTime": "2026-09-19T13:00:00",
            "Cancelled": "false", "Postponed": "False", "Abandoned": "0",
        })
        assert m.Cancelled is False
        assert m.Postponed is False
        assert m.Abandoned is False


# ---------------------------------------------------------------------------
# is_played
# ---------------------------------------------------------------------------

class TestIsPlayed:
    def _match(self, **overrides) -> IBISMatch:
        defaults = dict(
            MatchID=1,
            CompetitionID=100,
            CompetitionTypeID=1,
            HomeTeamID=4192,
            AwayTeamID=TEAM_A_ID,
            MatchDateTime="2026-09-19T13:00:00",
            Cancelled=False,
            Postponed=False,
            Abandoned=False,
        )
        return IBISMatch(**{**defaults, **overrides})

    def test_spelad_med_final_result_ts(self):
        m = self._match(FinalResultCreatedTS="2026-09-19T15:30:00", GoalsHomeTeam=3, GoalsAwayTeam=2)
        assert is_played(m) is True

    def test_instaelld_raeknas_inte(self):
        m = self._match(Cancelled=True, FinalResultCreatedTS="2026-09-19T15:30:00")
        assert is_played(m) is False

    def test_cup_raeknas_inte(self):
        m = self._match(CompetitionTypeID=3, FinalResultCreatedTS="2026-09-19T15:30:00")
        assert is_played(m) is False

    def test_schemalagd_framtida_raeknas_inte(self):
        # Framtida match, ingen result, inga mål
        m = self._match(MatchDateTime="2099-01-01T13:00:00")
        assert is_played(m) is False

    def test_passerad_med_mal_raeknas(self):
        # Passerat datum + GoalsHomeTeam satt → spelad (FinalResultCreatedTS kan saknas)
        m = self._match(MatchDateTime="2020-01-01T13:00:00", GoalsHomeTeam=2, GoalsAwayTeam=1)
        assert is_played(m) is True

    def test_passerad_utan_mal_raeknas_inte(self):
        # Passerat datum men inga mål → ej rapporterad
        m = self._match(MatchDateTime="2020-01-01T13:00:00", GoalsHomeTeam=None)
        assert is_played(m) is False

    def test_abandoned_med_resultat_raeknas(self):
        m = self._match(Abandoned=True, FinalResultCreatedTS="2026-09-19T14:00:00", GoalsHomeTeam=1, GoalsAwayTeam=0)
        assert is_played(m) is True

    def test_abandoned_utan_resultat_raeknas_inte(self):
        m = self._match(Abandoned=True)
        assert is_played(m) is False


# ---------------------------------------------------------------------------
# filter_series_competitions
# ---------------------------------------------------------------------------

class TestFilterSeriesCompetitions:
    def test_team_a_har_en_serietaevling(self, team_a):
        series = filter_series_competitions(team_a)
        assert len(series) == 1
        assert series[0].CompetitionTypeID == 1
        assert "Division 2" in series[0].Name

    def test_team_b_har_en_serietaevling(self, team_b):
        series = filter_series_competitions(team_b)
        assert len(series) == 1
        assert series[0].CompetitionTypeID == 1

    def test_filtrerar_bort_cup(self, team_a):
        series = filter_series_competitions(team_a)
        assert all(c.CompetitionTypeID == 1 for c in series)

    def test_team_a_har_matcher(self, team_a):
        series = filter_series_competitions(team_a)
        assert len(series[0].Matches) > 0

    def test_tomt_lag_ger_tom_lista(self):
        team = IBISTeam(TeamID=1, Name="Test", Competitions=[])
        assert filter_series_competitions(team) == []


# ---------------------------------------------------------------------------
# get_team_players
# ---------------------------------------------------------------------------

class TestGetTeamPlayers:
    def test_returnerar_bortalagets_spelare(self, lineups_played):
        # Tungelsta A är bortalaget i lineups_played (AwayTeamID=1977)
        players = get_team_players(lineups_played, TEAM_A_ID)
        ids = [p.PlayerID for p in players]
        assert 490153 in ids   # Theodor Landell
        assert 143653 in ids   # Marvin Rickman

    def test_returnerar_hemmalag_spelare(self, lineups_played):
        players = get_team_players(lineups_played, lineups_played.HomeTeamID)
        assert len(players) >= 1

    def test_fel_team_id_ger_exception(self, lineups_played):
        with pytest.raises(ValueError, match="TeamID 9999"):
            get_team_players(lineups_played, 9999)

    def test_tom_trupp_ger_tom_lista(self, lineups_scheduled):
        players = get_team_players(lineups_scheduled, TEAM_A_ID)
        assert players == []


# ---------------------------------------------------------------------------
# IBISTeam och IBISCompetition parsing från riktig fixture
# ---------------------------------------------------------------------------

class TestTeamParsing:
    def test_team_a_parsas(self, team_a):
        assert team_a.TeamID == TEAM_A_ID
        assert "Tungelsta" in team_a.Name

    def test_team_b_parsas(self, team_b):
        assert team_b.TeamID == TEAM_B_ID

    def test_team_a_matcher_har_ratt_typ(self, team_a):
        series = filter_series_competitions(team_a)
        for m in series[0].Matches:
            assert isinstance(m.MatchID, int)
            assert isinstance(m.Cancelled, bool)
            assert isinstance(m.MatchDateTime, str)

    def test_match_datetime_parsas(self, team_a):
        series = filter_series_competitions(team_a)
        first = series[0].Matches[0]
        dt = parse_kickoff(first.MatchDateTime)
        assert dt.year >= 2026


# ---------------------------------------------------------------------------
# IBISLineups parsing
# ---------------------------------------------------------------------------

class TestLineupsParsing:
    def test_played_lineup_parsas(self, lineups_played):
        assert lineups_played.MatchID == 1703100
        assert lineups_played.AwayTeamID == TEAM_A_ID
        assert len(lineups_played.AwayTeamPlayers) == 3

    def test_scheduled_lineup_ar_tom(self, lineups_scheduled):
        assert lineups_scheduled.HomeTeamPlayers == []
        assert lineups_scheduled.AwayTeamPlayers == []

    def test_spelare_falt(self, lineups_played):
        p = lineups_played.AwayTeamPlayers[0]
        assert isinstance(p.PlayerID, int)
        assert isinstance(p.Name, str)


# ---------------------------------------------------------------------------
# ShirtNo och andra fält där iBIS är inkonsekvent mellan endpoints
#
# ShirtNo kommer som int i lineups-svaret men som str i lagobjektets Players[].
# Modellerna ska acceptera båda och normalisera till str. null ska fungera.
# ---------------------------------------------------------------------------

class TestShirtNoCoercion:
    def _match_player(self, shirt) -> IBISMatchPlayer:
        return IBISMatchPlayer.model_validate({
            "PlayerID": 1, "MatchPlayerID": 100, "Name": "Test", "ShirtNo": shirt,
        })

    def _squad_player(self, shirt) -> IBISSquadPlayer:
        return IBISSquadPlayer.model_validate({
            "PlayerID": 1, "Name": "Test", "ShirtNo": shirt,
        })

    def test_matchspelare_shirtno_int_blir_str(self):
        assert self._match_player(5).ShirtNo == "5"

    def test_matchspelare_shirtno_str_oforandrad(self):
        assert self._match_player("5").ShirtNo == "5"

    def test_matchspelare_shirtno_int_och_str_ger_samma(self):
        assert self._match_player(5).ShirtNo == self._match_player("5").ShirtNo

    def test_matchspelare_shirtno_null(self):
        assert self._match_player(None).ShirtNo is None

    def test_truppspelare_shirtno_int_blir_str(self):
        assert self._squad_player(5).ShirtNo == "5"

    def test_truppspelare_shirtno_str_oforandrad(self):
        assert self._squad_player("5").ShirtNo == "5"

    def test_truppspelare_shirtno_int_och_str_ger_samma(self):
        assert self._squad_player(5).ShirtNo == self._squad_player("5").ShirtNo

    def test_truppspelare_shirtno_null(self):
        assert self._squad_player(None).ShirtNo is None

    def test_lineups_med_int_shirtno_validerar(self):
        # Konkret fall ur buggrapporten: iBIS skickar ShirtNo som int i
        # lineups-svaret. Tidigare kraschade hela IBISLineups-valideringen.
        lineups = IBISLineups.model_validate({
            "MatchID": 1, "HomeTeamID": 1, "AwayTeamID": 2,
            "HomeTeamPlayers": [
                {"PlayerID": 5, "MatchPlayerID": 50, "Name": "Femman", "ShirtNo": 5},
            ],
            "AwayTeamPlayers": [],
        })
        assert lineups.HomeTeamPlayers[0].ShirtNo == "5"

    def test_tomt_talfalt_blir_none(self):
        # iBIS skickar ibland "" i stället för null för tomma talfält.
        p = IBISMatchPlayer.model_validate({
            "PlayerID": 1, "MatchPlayerID": 100, "Name": "Test",
            "Goals": "", "Assists": "  ", "PenaltyMinutes": None,
        })
        assert p.Goals is None
        assert p.Assists is None
        assert p.PenaltyMinutes is None

    def test_talfalt_som_strang_tolkas(self):
        p = IBISMatchPlayer.model_validate({
            "PlayerID": 1, "MatchPlayerID": 100, "Name": "Test",
            "Goals": "2", "Assists": "1",
        })
        assert p.Goals == 2
        assert p.Assists == 1

    def test_match_resultat_som_strang_tolkas(self):
        m = IBISMatch.model_validate({
            "MatchID": 1, "CompetitionID": 100, "CompetitionTypeID": 1,
            "HomeTeamID": 1, "AwayTeamID": 2,
            "MatchDateTime": "2026-09-19T13:00:00",
            "Cancelled": False, "Postponed": False, "Abandoned": False,
            "GoalsHomeTeam": "3", "GoalsAwayTeam": "",
        })
        assert m.GoalsHomeTeam == 3
        assert m.GoalsAwayTeam is None
