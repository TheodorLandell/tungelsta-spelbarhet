"""
Regelmotor for representationsregler, Tungelsta IF.

Tva regler:
  1. Kvalificering: en spelare maste sta uppskriven i en B-lagsmatch innan
     sin forsta A-lagsmatch. Annars last i A-laget (far ej spela B).
  2. Kedja: max 2 A-lagsmatcher i rad. Den tredje i rad laser spelaren.
     Kedjan nollstalls bara av att sta over en A-match. B-matcher paverkar inte.

Instalda matcher hoppas over helt.
Rakningen sker pa faktiskt speldatum, inte omgangsnummer.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

MAX_CONSECUTIVE_A = 2  # den (MAX + 1):e matchen i rad laser


class Team(Enum):
    A = "A"  # Herrar Division 2
    B = "B"  # Herrar Division 5 Sydostra


class MatchStatus(Enum):
    PLAYED = "played"
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"


class LockReason(Enum):
    NO_B_MATCH_FIRST = "spelade A-match innan nagon B-match"
    THREE_IN_A_ROW = "tre A-matcher i rad"


@dataclass(frozen=True)
class Match:
    match_id: int
    team: Team
    kickoff: datetime
    status: MatchStatus = MatchStatus.PLAYED


@dataclass(frozen=True)
class Appearance:
    """En spelare uppskriven i en matchtrupp. Fran API:ts lineups-endpoint."""
    match_id: int
    player_id: int
    player_name: str


@dataclass
class PlayerStatus:
    player_id: int
    player_name: str
    locked: bool = False
    lock_reason: LockReason | None = None
    lock_match_id: int | None = None
    lock_date: datetime | None = None
    consecutive_a: int = 0
    has_b_appearance: bool = False
    a_match_ids: list[int] = field(default_factory=list)
    b_match_ids: list[int] = field(default_factory=list)

    @property
    def matches_left(self) -> int | None:
        """Antal A-matcher kvar innan lasning. None om redan last."""
        if self.locked:
            return None
        return MAX_CONSECUTIVE_A - self.consecutive_a

    @property
    def warning(self) -> str:
        if self.locked:
            return "LAST"
        left = self.matches_left
        if left == 0:
            return "MASTE STA OVER"
        if left == 1:
            return "SISTA MATCHEN"
        return "OK"


def compute_statuses(
    matches: list[Match],
    appearances: list[Appearance],
    *,
    include_scheduled: bool = False,
    strict_ties: bool = True,
) -> tuple[dict[int, PlayerStatus], list[str]]:
    """
    Rakna fram status for varje spelare.

    include_scheduled: ta med matcher som annu inte spelats. Anvands for
        simulering ("vad hander om jag skriver upp honom pa lordag?").
    strict_ties: om en A- och en B-match har exakt samma kickoff, rakna
        A-matchen forst. Konservativt: risken ar da att appen sager LAST
        for tidigt, vilket ar battre an att slappa fram en olaglig spelare.

    Returnerar (status per player_id, lista med varningar att visa for tranaren).
    """
    warnings: list[str] = []

    allowed = {MatchStatus.PLAYED}
    if include_scheduled:
        allowed.add(MatchStatus.SCHEDULED)
    live = [m for m in matches if m.status in allowed]

    by_match: dict[int, list[Appearance]] = {}
    for app in appearances:
        by_match.setdefault(app.match_id, []).append(app)

    # Konservativ sortering: vid exakt samma kickoff kommer A fore B.
    def sort_key(m: Match):
        tie = 0 if m.team is Team.A else 1
        return (m.kickoff, tie if strict_ties else 1 - tie)

    live.sort(key=sort_key)

    kickoffs: dict[datetime, set[Team]] = {}
    for m in live:
        kickoffs.setdefault(m.kickoff, set()).add(m.team)
    for when, teams in kickoffs.items():
        if len(teams) > 1:
            warnings.append(
                f"A- och B-match med identisk starttid {when:%Y-%m-%d %H:%M} "
                "- ordningen ar en gissning, kontrollera manuellt."
            )

    statuses: dict[int, PlayerStatus] = {}

    def ensure(app: Appearance) -> PlayerStatus:
        st = statuses.get(app.player_id)
        if st is None:
            st = PlayerStatus(app.player_id, app.player_name)
            statuses[app.player_id] = st
        else:
            st.player_name = app.player_name
        return st

    for match in live:
        squad = by_match.get(match.match_id, [])

        if match.team is Team.B:
            for app in squad:
                st = ensure(app)
                st.has_b_appearance = True
                st.b_match_ids.append(match.match_id)
            continue

        # A-match. Registrera forst alla i truppen sa de finns i statuses.
        in_squad = set()
        for app in squad:
            st = ensure(app)
            in_squad.add(st.player_id)

        for st in statuses.values():
            if st.locked:
                continue

            if st.player_id not in in_squad:
                st.consecutive_a = 0
                continue

            st.a_match_ids.append(match.match_id)

            if not st.has_b_appearance:
                st.locked = True
                st.lock_reason = LockReason.NO_B_MATCH_FIRST
                st.lock_match_id = match.match_id
                st.lock_date = match.kickoff
                continue

            st.consecutive_a += 1
            if st.consecutive_a > MAX_CONSECUTIVE_A:
                st.locked = True
                st.lock_reason = LockReason.THREE_IN_A_ROW
                st.lock_match_id = match.match_id
                st.lock_date = match.kickoff

    return statuses, warnings


def blocked_for_b(statuses: dict[int, PlayerStatus]) -> list[PlayerStatus]:
    """Spelare som inte langre far anvandas i B-laget."""
    return sorted(
        (s for s in statuses.values() if s.locked),
        key=lambda s: s.player_name,
    )


def available_for_b(statuses: dict[int, PlayerStatus]) -> list[PlayerStatus]:
    """Spelare som fortfarande far spela B, sorterade sa de mest utsatta hamnar overst."""
    return sorted(
        (s for s in statuses.values() if not s.locked),
        key=lambda s: (s.matches_left, s.player_name),
    )
