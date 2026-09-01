"""
Klient mot iBIS publika API.

Hämtar token anonymt via /StatsAppApi/api/startkit innan varje session.
Anrop är sekventiella med kort paus och retry med exponentiell backoff.
"""

import time
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, field_validator

BASE_URL = "https://api.innebandy.se/v2/api"
STARTKIT_URL = "https://api.innebandy.se/StatsAppApi/api/startkit"
STATS_ORIGIN = "https://stats.innebandy.se"

STOCKHOLM = timezone(timedelta(hours=2))   # sommartid; ZoneInfo används inte för att undvika extern dep
REQUEST_PAUSE = 0.5        # sekunder mellan anrop
TIMEOUT = 15.0
MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# Pydantic-modeller – fältnamn matchar iBIS exakt
# ---------------------------------------------------------------------------
#
# iBIS är inte konsekvent mellan endpoints: samma fält kan komma som int i ett
# svar och som str i ett annat (ShirtNo är int i lineups men str i lagobjektets
# Players[]), och tomma talfält kan komma som "" i stället för null. Modellerna
# är därför avsiktligt toleranta – hellre normalisera än att avbryta synken mitt
# i en match. Två hjälpvalidatorer används genomgående:
#   _coerce_str      – normaliserar tal/sträng till str (för visningsfält)
#   _coerce_opt_int  – tomma strängar → None, i övrigt låt pydantic tolka talet


def _coerce_str(v: Any) -> Any:
    """Normaliserar ett fält som kan komma som int eller str till str.

    None förblir None. Heltalsflyttal (5.0) blir "5". Andra typer lämnas
    orörda så att pydantic får klaga på det som verkligen är fel.
    """
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, bool):
        return v  # låt pydantic avvisa – bool i ett strängfält är alltid fel
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    return v


def _coerce_opt_int(v: Any) -> Any:
    """Gör ett valfritt heltalsfält tolerant mot iBIS-inkonsekvens.

    "" och blanktecken → None. Andra strängar ("5") lämnas till pydantic som
    tolkar dem som tal i lax-läge. Icke-strängar lämnas orörda.
    """
    if isinstance(v, str):
        s = v.strip()
        return None if s == "" else s
    return v


class IBISMatchPlayer(BaseModel):
    PlayerID: int
    MatchPlayerID: int
    Name: str
    ShirtNo: str | None = None
    Goals: int | None = None
    Assists: int | None = None
    PenaltyMinutes: int | None = None
    PositionID: int | None = None
    Position: str | None = None
    LicensedAssociationID: int | None = None

    @field_validator("ShirtNo", mode="before")
    @classmethod
    def _shirt_to_str(cls, v: Any) -> Any:
        return _coerce_str(v)

    @field_validator(
        "Goals", "Assists", "PenaltyMinutes", "PositionID",
        "LicensedAssociationID", mode="before",
    )
    @classmethod
    def _opt_int(cls, v: Any) -> Any:
        return _coerce_opt_int(v)


class IBISMatch(BaseModel):
    MatchID: int
    CompetitionID: int
    CompetitionTypeID: int
    HomeTeamID: int
    HomeTeam: str | None = None
    AwayTeamID: int
    AwayTeam: str | None = None
    MatchDateTime: str          # naiv lokaltid som sträng, tolkas separat
    Cancelled: bool
    Postponed: bool
    Abandoned: bool
    GoalsHomeTeam: int | None = None
    GoalsAwayTeam: int | None = None
    FinalResultCreatedTS: str | None = None
    Round: int | None = None
    RoundName: str | None = None
    MatchStatus: int | None = None

    @field_validator("Cancelled", "Postponed", "Abandoned", mode="before")
    @classmethod
    def coerce_bool(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        return str(v).lower() in ("true", "1", "yes")

    @field_validator(
        "GoalsHomeTeam", "GoalsAwayTeam", "Round", "MatchStatus", mode="before",
    )
    @classmethod
    def _opt_int(cls, v: Any) -> Any:
        return _coerce_opt_int(v)

    @field_validator("HomeTeam", "AwayTeam", "RoundName", mode="before")
    @classmethod
    def _str_fields(cls, v: Any) -> Any:
        return _coerce_str(v)


class IBISCompetition(BaseModel):
    CompetitionID: int
    CompetitionTypeID: int
    Name: str
    Matches: list[IBISMatch] = []


class IBISSquadPlayer(BaseModel):
    PlayerID: int
    Name: str
    # ShirtNo kommer som str här men som int i lineups – normalisera alltid
    # till str så resten av koden slipper bry sig om varifrån spelaren kom.
    ShirtNo: str | None = None
    PositionID: int | None = None
    Position: str | None = None

    @field_validator("ShirtNo", mode="before")
    @classmethod
    def _shirt_to_str(cls, v: Any) -> Any:
        return _coerce_str(v)

    @field_validator("PositionID", mode="before")
    @classmethod
    def _opt_int(cls, v: Any) -> Any:
        return _coerce_opt_int(v)


class IBISTeam(BaseModel):
    TeamID: int
    Name: str
    Competitions: list[IBISCompetition] = []
    Players: list[IBISSquadPlayer] = []


class IBISLineups(BaseModel):
    MatchID: int
    HomeTeamID: int
    AwayTeamID: int
    HomeTeamPlayers: list[IBISMatchPlayer] = []
    AwayTeamPlayers: list[IBISMatchPlayer] = []


# ---------------------------------------------------------------------------
# Hjälpfunktioner
# ---------------------------------------------------------------------------

def parse_kickoff(match_datetime: str) -> datetime:
    """Tolkar naiv iBIS-tidssträng som Europe/Stockholm-lokaltid."""
    naive = datetime.fromisoformat(match_datetime)
    return naive.replace(tzinfo=STOCKHOLM)


def is_played(match: IBISMatch) -> bool:
    """
    Avsnitt 3.4: en match räknas som spelad om alla stämmer:
    - Cancelled == False
    - CompetitionTypeID == 1
    - FinalResultCreatedTS är satt, ELLER (kickoff har passerat och GoalsHomeTeam != null)
    """
    if match.Cancelled:
        return False
    if match.CompetitionTypeID != 1:
        return False
    if match.FinalResultCreatedTS:
        return True
    kickoff = parse_kickoff(match.MatchDateTime)
    now = datetime.now(tz=STOCKHOLM)
    return kickoff < now and match.GoalsHomeTeam is not None


def filter_series_competitions(team: IBISTeam) -> list[IBISCompetition]:
    """Returnerar bara tävlingar med CompetitionTypeID == 1 (seriematcher)."""
    return [c for c in team.Competitions if c.CompetitionTypeID == 1]


# Kända målvaktsbeteckningar i Position-fältet. PositionID:s enum är
# odokumenterad (lag-Players[] har 1 = Målvakt men lineups har inte bekräftat
# samma värde), så målvakt avgörs på den läsbara texten. Saknas Position är
# spelaren inte målvakt.
_GOALKEEPER_POSITIONS = {"mv", "mål", "malvakt", "målvakt", "goalkeeper", "goalie", "gk", "g"}


def is_goalkeeper_player(player: "IBISMatchPlayer | IBISSquadPlayer") -> bool:
    """Avgör om en spelare är målvakt utifrån Position (lineup eller trupp)."""
    pos = (player.Position or "").strip().lower()
    if not pos:
        return False
    return pos in _GOALKEEPER_POSITIONS or "målvakt" in pos or "goalkeeper" in pos


def get_team_players(lineups: IBISLineups, team_id: int) -> list[IBISMatchPlayer]:
    """Väljer rätt spelararray baserat på om laget är hemma eller borta."""
    if lineups.HomeTeamID == team_id:
        return lineups.HomeTeamPlayers
    if lineups.AwayTeamID == team_id:
        return lineups.AwayTeamPlayers
    raise ValueError(f"TeamID {team_id} finns varken som hemma ({lineups.HomeTeamID}) eller borta ({lineups.AwayTeamID})")


# ---------------------------------------------------------------------------
# HTTP-session med token och retry
# ---------------------------------------------------------------------------

class IBISClient:
    """Klient som hanterar token, retry och paus mellan anrop."""

    def __init__(
        self,
        *,
        timeout: float = TIMEOUT,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self._token: str | None = None
        self._token_expiry: datetime | None = None
        # Live-endpointen (SPEC 6.6) skickar en klient med kort timeout och
        # utan omförsök, så att en trög iBIS aldrig får klienten att hänga.
        self._timeout = timeout
        self._max_retries = max_retries

    def _ensure_token(self) -> str:
        now = datetime.now(tz=timezone.utc)
        if self._token and self._token_expiry and self._token_expiry > now:
            return self._token

        resp = self._raw_get(STARTKIT_URL)
        data = resp.json()
        self._token = data["accessToken"]
        expiry_str = data["accessTokenExpiration"]
        # Expiry kan ha offset (+02:00). datetime.fromisoformat hanterar det i 3.11+.
        self._token_expiry = datetime.fromisoformat(expiry_str).astimezone(timezone.utc)
        return self._token

    def _raw_get(self, url: str, headers: dict | None = None) -> httpx.Response:
        h = {"Origin": STATS_ORIGIN, "Referer": f"{STATS_ORIGIN}/", **(headers or {})}
        for attempt in range(self._max_retries):
            try:
                resp = httpx.get(url, headers=h, timeout=self._timeout)
                resp.raise_for_status()
                return resp
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                if attempt == self._max_retries - 1:
                    raise
                backoff = 2 ** attempt
                time.sleep(backoff)
        raise RuntimeError("Oväntat slut på retry-loop")  # nås inte

    def _get(self, path: str) -> httpx.Response:
        token = self._ensure_token()
        time.sleep(REQUEST_PAUSE)
        return self._raw_get(
            f"{BASE_URL}/{path}",
            headers={"Authorization": f"Bearer {token}"},
        )

    def fetch_team(self, season_id: int, team_id: int) -> IBISTeam:
        resp = self._get(f"seasons/{season_id}/teams/{team_id}")
        return IBISTeam.model_validate(resp.json())

    def fetch_team_raw(self, season_id: int, team_id: int) -> dict:
        """Returnerar råa API-svaret som dict (för lagring i raw-kolumnen)."""
        resp = self._get(f"seasons/{season_id}/teams/{team_id}")
        return resp.json()

    def fetch_lineups(self, match_id: int) -> IBISLineups:
        resp = self._get(f"matches/{match_id}/lineups")
        return IBISLineups.model_validate(resp.json())
