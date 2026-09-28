"""
Klient mot iBIS publika API.

Hämtar token anonymt via /StatsAppApi/api/startkit innan varje session.
Anrop är sekventiella med kort paus och retry med exponentiell backoff.
"""

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, field_validator

from app.config import settings

# Bas-URL:en kommer från konfigurationen (IBIS_BASE_URL). iBIS har flyttat
# endpointerna en gång – den gamla sökvägen utan /public/ svarar nu 403 på
# samtliga anrop – så nästa flytt ska bara kräva en miljövariabel. Eventuellt
# avslutande snedstreck tas bort, för _get lägger på ett eget.
BASE_URL = settings.ibis_base_url.rstrip("/")
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
    # iBIS sätter detta när datum/tid ännu inte bestämts. Saknas fältet är
    # platshållaren MatchDateTime "1 januari 00:00" (se is_date_missing).
    MatchTimeMissing: bool = False

    @field_validator(
        "Cancelled", "Postponed", "Abandoned", "MatchTimeMissing", mode="before"
    )
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
    # Lineup-svaret under /v2/api/public har bara de två spelararrayerna kvar:
    # MatchID, HomeTeamID och AwayTeamID är borta, och per spelare saknas
    # Goals, Assists, PenaltyMinutes och Position. Fälten är därför valfria –
    # vilken array som är vår avgörs numera av matchobjektet (get_team_players
    # med is_home) och statistiken räknas ur Events (player_stats_from_events).
    MatchID: int | None = None
    HomeTeamID: int | None = None
    AwayTeamID: int | None = None
    HomeTeamPlayers: list[IBISMatchPlayer] = []
    AwayTeamPlayers: list[IBISMatchPlayer] = []

    @field_validator("HomeTeamPlayers", "AwayTeamPlayers", mode="before")
    @classmethod
    def _players_list(cls, v: Any) -> Any:
        """En trupp som inte publicerats kommer som null, inte som tom lista.

        Gäller en sida i taget: en match kan ha hemmalagets trupp uppe men inte
        bortalagets. Utan normalisering avbryts hela matchen på ett fel som
        egentligen bara betyder "inget publicerat än".
        """
        return [] if v is None else v


# Matchhändelser. MatchEventTypeID är en odokumenterad enum; de två vi bryr oss
# om är mål och utvisning. Resten (periodstart/slut, timeout, målvaktsbyten)
# ignoreras.
EVENT_GOAL = 1
EVENT_PENALTY = 2


class IBISMatchEvent(BaseModel):
    MatchEventID: int
    MatchEventTypeID: int
    Period: int | None = None
    Minute: int | None = None
    Second: int | None = None
    PlayerID: int | None = None
    PlayerAssistID: int | None = None
    PenaltyCode: str | None = None
    PenaltyName: str | None = None

    @field_validator(
        "Period", "Minute", "Second", "PlayerID", "PlayerAssistID", mode="before",
    )
    @classmethod
    def _opt_int(cls, v: Any) -> Any:
        return _coerce_opt_int(v)

    @field_validator("PenaltyCode", "PenaltyName", mode="before")
    @classmethod
    def _str_fields(cls, v: Any) -> Any:
        return _coerce_str(v)


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


# iBIS platshållare för en match där datum och tid ännu inte bestämts:
# MatchDateTime blir "1 januari 00:00". (månad, dag, timme, minut, sekund)
_PLACEHOLDER_KICKOFF = (1, 1, 0, 0, 0)


def is_date_missing(match: IBISMatch) -> bool:
    """
    True för en match där iBIS ännu inte satt ett riktigt datum.

    Använd MatchTimeMissing om fältet är satt, annars platshållaren där
    MatchDateTime är "1 januari 00:00". En sådan match får aldrig gå in i
    regelmotorn (oavsett kickoff) och visas sist i matchlistan.
    """
    if match.MatchTimeMissing:
        return True
    try:
        k = parse_kickoff(match.MatchDateTime)
    except ValueError:
        return False
    return (k.month, k.day, k.hour, k.minute, k.second) == _PLACEHOLDER_KICKOFF


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


# Utvisningens längd finns inte som eget fält i Events – bara som text i
# PenaltyName ("Slag, 2 min", "Hårt spel 2 min"). Minuterna läses därför ur
# texten. Går det inte att läsa (t.ex. "Matchstraff 1") returneras None, och
# minuterna hamnar som "okänd period" i stället för att gissas fel.
_PENALTY_MINUTES_RE = re.compile(r"(\d+)\s*min", re.IGNORECASE)
_ANY_NUMBER_RE = re.compile(r"\d+")


def penalty_minutes_from_name(name: str | None) -> int | None:
    """
    Läser antal minuter ur PenaltyName. None när texten inte säger något.

    Kravet är att texten innehåller exakt *ett* tal och att det talet följs av
    "min". Det avvisar både "Matchstraff 1" (tal utan minuter) och
    sammansatta utvisningar som "2+10 min", där ett enkelt uttryck skulle
    plocka fel tal. En okänd längd räknas som okänd period i stället för att
    gissas fel.
    """
    if not name:
        return None
    if len(_ANY_NUMBER_RE.findall(name)) != 1:
        return None
    found = _PENALTY_MINUTES_RE.findall(name)
    if len(found) != 1:
        return None
    return int(found[0])


def parse_match_events(raw_match: dict) -> list[IBISMatchEvent]:
    """
    Plockar ut mål- och utvisningshändelser ur ett rått matchobjekt.

    Events är null i lag-endpointen och fylls först i /matches/{id} när matchen
    spelats. Saknas de returneras en tom lista – anroparen får då ingen
    periodinformation och ska räkna målen bara i "hela matchen".
    """
    events = raw_match.get("Events")
    if not events:
        return []
    out: list[IBISMatchEvent] = []
    for e in events:
        if not isinstance(e, dict):
            continue
        if e.get("MatchEventTypeID") not in (EVENT_GOAL, EVENT_PENALTY):
            continue
        out.append(IBISMatchEvent.model_validate(e))
    return out


@dataclass
class PlayerMatchStats:
    """Mål, assist och utvisningsminuter för en spelare i en match."""
    goals: int = 0
    assists: int = 0
    penalty_minutes: int = 0


def player_stats_from_events(
    raw_match: dict, own_player_ids: set[int]
) -> dict[int, PlayerMatchStats] | None:
    """
    Räknar mål, assist och utvisningsminuter per spelare ur matchens Events.

    Lineups hade tidigare siffrorna per spelare, men under /v2/api/public är
    Goals, Assists och PenaltyMinutes borta ur svaret. Events är därför enda
    källan (SPEC 3.3). Bara våra egna spelare räknas – PlayerID är unikt, så
    motståndarnas händelser faller bort av sig själva.

    Returnerar None när Events är null, alltså när iBIS inte publicerat
    händelserna än. Då vet vi ingenting, och tidigare sparade siffror ska stå
    kvar hellre än att nollas. En tom lista är däremot ett riktigt svar: 0-0
    i en match som just börjat.

    Varning: utvisningsminuter utan läsbar längd i PenaltyName (t.ex.
    "Matchstraff 1") räknas som 0 minuter, eftersom minuterna inte finns som
    eget fält. Tidigare kom totalen färdigsummerad från iBIS.
    """
    if raw_match.get("Events") is None:
        return None
    stats = {pid: PlayerMatchStats() for pid in own_player_ids}
    for e in parse_match_events(raw_match):
        if e.MatchEventTypeID == EVENT_GOAL:
            if e.PlayerID in stats:
                stats[e.PlayerID].goals += 1
            if e.PlayerAssistID in stats:
                stats[e.PlayerAssistID].assists += 1
        elif e.MatchEventTypeID == EVENT_PENALTY and e.PlayerID in stats:
            stats[e.PlayerID].penalty_minutes += (
                penalty_minutes_from_name(e.PenaltyName) or 0
            )
    return stats


def get_team_players(
    lineups: IBISLineups,
    team_id: int,
    *,
    is_home: bool | None = None,
) -> list[IBISMatchPlayer]:
    """
    Väljer rätt spelararray baserat på om laget är hemma eller borta.

    Lineup-svaret innehåller inte längre HomeTeamID/AwayTeamID, så sidan måste
    komma från matchobjektet via is_home. Har svaret ID:na kvar avgörs sidan av
    dem som tidigare, så äldre sparade svar fungerar fortfarande.
    """
    if is_home is not None:
        return lineups.HomeTeamPlayers if is_home else lineups.AwayTeamPlayers
    if lineups.HomeTeamID is not None and lineups.HomeTeamID == team_id:
        return lineups.HomeTeamPlayers
    if lineups.AwayTeamID is not None and lineups.AwayTeamID == team_id:
        return lineups.AwayTeamPlayers
    raise ValueError(
        f"TeamID {team_id} finns varken som hemma ({lineups.HomeTeamID}) eller "
        f"borta ({lineups.AwayTeamID}) – saknar svaret ID:na måste is_home anges"
    )


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

    def fetch_match_raw(self, match_id: int) -> dict:
        """
        Hämtar ett enskilt matchobjekt.

        Behövs för Events[], som är null i lag-endpointen och bara fylls här.
        Det är enda källan till vilken period ett mål eller en utvisning hör
        till (SPEC 6.7).
        """
        resp = self._get(f"matches/{match_id}")
        return resp.json()
