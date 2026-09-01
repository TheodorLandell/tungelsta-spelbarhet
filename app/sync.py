"""
Synkjobb: hämtar matchdata och lineups från iBIS och sparar i databasen.

Kör manuellt: python -m app.sync
"""

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, engine
from app.ibis_client import (
    IBISClient,
    IBISMatch,
    IBISMatchPlayer,
    IBISSquadPlayer,
    IBISTeam,
    get_team_players,
    is_goalkeeper_player,
    is_played,
    parse_kickoff,
)
from app.models import Appearance, Base, Match, Player, PlayerTeam, SyncLog

STOCKHOLM = timezone(timedelta(hours=2))


@dataclass
class SyncResult:
    """Värden lästa ur SyncLog medan sessionen fortfarande var öppen."""
    ok: bool
    matches_added: int
    warnings: list[str]
    started_at: datetime
    finished_at: datetime
    log_id: int


def _now_naive() -> datetime:
    return datetime.now(tz=STOCKHOLM).replace(tzinfo=None)


def _match_status(m: IBISMatch) -> str:
    if m.Cancelled:
        return "cancelled"
    if is_played(m):
        return "played"
    return "scheduled"


def _opponent(m: IBISMatch, team_id: int) -> str | None:
    if m.HomeTeamID == team_id:
        return m.AwayTeam
    if m.AwayTeamID == team_id:
        return m.HomeTeam
    return None


def _upsert_match(
    db: Session,
    m: IBISMatch,
    team_label: str,
    team_id: int,
    raw: dict,
    *,
    counts_for_rules: bool = True,
) -> tuple[Match, bool]:
    """Sparar eller uppdaterar en match. Returnerar (orm-objekt, är_ny)."""
    existing = db.get(Match, m.MatchID)
    status = _match_status(m)
    kickoff = parse_kickoff(m.MatchDateTime).replace(tzinfo=None)

    if existing is None:
        match = Match(
            match_id=m.MatchID,
            team=team_label,
            competition_id=m.CompetitionID,
            kickoff=kickoff,
            status=status,
            round_name=m.RoundName,
            opponent=_opponent(m, team_id),
            counts_for_rules=counts_for_rules,
            raw=raw,
        )
        db.add(match)
        return match, True

    existing.status = status
    existing.counts_for_rules = counts_for_rules
    existing.raw = raw
    return existing, False


def _upsert_player(db: Session, p: IBISMatchPlayer, kickoff: datetime) -> None:
    is_gk = is_goalkeeper_player(p)
    has_position = bool((p.Position or "").strip())
    existing = db.get(Player, p.PlayerID)
    if existing is None:
        db.add(Player(
            player_id=p.PlayerID,
            name=p.Name,
            shirt_no=p.ShirtNo,
            is_goalkeeper=is_gk,
            last_seen=kickoff,
        ))
    else:
        existing.name = p.Name
        if p.ShirtNo is not None:
            existing.shirt_no = p.ShirtNo
        # Uppdatera bara målvaktsmarkeringen när lineupen faktiskt har
        # positionsdata – annars skulle en tom position nolla en tidigare
        # känd målvakt.
        if has_position:
            existing.is_goalkeeper = is_gk
        if kickoff > existing.last_seen:
            existing.last_seen = kickoff


def _upsert_squad_player(db: Session, p: IBISSquadPlayer, sync_at: datetime) -> None:
    """Sparar en trupp-spelare som ännu inte förekommer i någon lineup."""
    shirt = str(p.ShirtNo) if p.ShirtNo is not None else None
    is_gk = is_goalkeeper_player(p)
    has_position = bool((p.Position or "").strip())
    existing = db.get(Player, p.PlayerID)
    if existing is None:
        db.add(Player(
            player_id=p.PlayerID,
            name=p.Name,
            shirt_no=shirt,
            is_goalkeeper=is_gk,
            last_seen=sync_at,
        ))
    else:
        existing.name = p.Name
        if shirt is not None:
            existing.shirt_no = shirt
        if has_position:
            existing.is_goalkeeper = is_gk


def _upsert_player_team(db: Session, player_id: int, team_label: str) -> None:
    """Registrerar att en spelare hör till ett lag. Idempotent."""
    if db.get(PlayerTeam, (player_id, team_label)) is None:
        db.add(PlayerTeam(player_id=player_id, team=team_label))


def _sync_player_teams(db: Session, team_label: str) -> None:
    """
    Fyller player_teams för ett lag som unionen av två källor:
      - spelare med en appearance i en match som tillhör laget
      - spelare i lagets Players[] (registreras separat i squad-loopen)
    """
    player_ids = db.scalars(
        select(Appearance.player_id)
        .join(Match, Match.match_id == Appearance.match_id)
        .where(Match.team == team_label)
        .distinct()
    ).all()
    for pid in player_ids:
        _upsert_player_team(db, pid, team_label)


def _has_appearances(db: Session, match_id: int) -> bool:
    return db.scalars(
        select(Appearance.player_id)
        .where(Appearance.match_id == match_id)
        .limit(1)
    ).first() is not None


def _save_appearances(
    db: Session,
    match_id: int,
    players: list[IBISMatchPlayer],
    kickoff: datetime,
) -> None:
    for p in players:
        _upsert_player(db, p, kickoff)
        goals = p.Goals or 0
        assists = p.Assists or 0
        penalty_minutes = p.PenaltyMinutes or 0
        existing = db.get(Appearance, (match_id, p.PlayerID))
        if existing is None:
            db.add(Appearance(
                match_id=match_id,
                player_id=p.PlayerID,
                player_name=p.Name,
                shirt_no=p.ShirtNo,
                goals=goals,
                assists=assists,
                penalty_minutes=penalty_minutes,
            ))
        else:
            # Befintlig appearance: uppdatera statistiken från iBIS.
            existing.goals = goals
            existing.assists = assists
            existing.penalty_minutes = penalty_minutes


def run_sync(db: Session, client: IBISClient) -> SyncResult:
    """
    Hämtar matchdata från iBIS och sparar i databasen.
    Returnerar SyncResult med värden lästa medan sessionen var öppen.
    ORM-objekt lämnar aldrig funktionen.
    """
    log = SyncLog(
        started_at=_now_naive(),
        finished_at=None,
        matches_added=0,
        warnings=[],
        ok=False,
    )
    db.add(log)
    db.flush()

    warnings: list[str] = []
    matches_added = 0

    try:
        for team_label, team_id in (("A", settings.team_a_id), ("B", settings.team_b_id)):
            db.flush()  # gör föregående lags pending-objekt synliga för get()
            raw_team = client.fetch_team_raw(settings.season_id, team_id)
            team = IBISTeam.model_validate(raw_team)

            # Bygg uppslag match_id → rådata för alla matcher (även cup/träning)
            raw_by_match: dict[int, dict] = {}
            for comp_data in raw_team.get("Competitions", []):
                for m_data in comp_data.get("Matches", []):
                    raw_by_match[m_data["MatchID"]] = m_data

            # Alla tävlingar, inte bara serien. Matcher med annan
            # CompetitionTypeID sparas för skottregistrering men markeras med
            # counts_for_rules = False och rör aldrig regelmotorn.
            for comp in team.Competitions:
                counts_for_rules = comp.CompetitionTypeID == 1
                for match in comp.Matches:
                    raw = raw_by_match.get(match.MatchID, match.model_dump(mode="json"))
                    db_match, is_new = _upsert_match(
                        db, match, team_label, team_id, raw,
                        counts_for_rules=counts_for_rules,
                    )

                    if is_new:
                        matches_added += 1

                    # Inställda matcher hoppas över helt (SPEC punkt 1) – de
                    # spelas aldrig och får aldrig en publicerad trupp.
                    if match.Cancelled:
                        continue

                    # Avbruten utan resultat: logga varning, hoppa över appearances
                    if match.Abandoned and not is_played(match):
                        warnings.append(
                            f"Match {match.MatchID} ({match.MatchDateTime[:10]}): "
                            "avbruten utan registrerat resultat – hoppas över"
                        )
                        continue

                    # Truppen publiceras i iBIS före matchstart, så lineups
                    # hämtas även för matcher som ännu inte spelats. Är lineups
                    # tom sparas inget (players blir []). Det är counts_for_rules
                    # (satt ovan) som avgör om matchen når regelmotorn, se
                    # app/status.py – sync.py sparar bara underlaget.
                    kickoff = parse_kickoff(match.MatchDateTime).replace(tzinfo=None)

                    # En färdigrapporterad match som redan har appearances
                    # hämtas inte om (SPEC 3.5). Allt annat hämtas: matcher utan
                    # appearances, och spelade matcher som ännu inte är
                    # färdigrapporterade – så statistiken hålls färsk om
                    # sekretariatet rättar något i efterhand.
                    if (
                        match.FinalResultCreatedTS
                        and _has_appearances(db, match.MatchID)
                    ):
                        continue

                    # Kommande matcher: trupper publiceras inte tidigare än sju
                    # dagar före kickoff, så matcher längre bort än så hämtas
                    # inte – annars hämtas lineups för hela säsongen vid varje
                    # synk och jobbet tar minuter. Spelade matcher berörs inte
                    # av tidsgränsen; de följer regeln ovan oavsett hur långt
                    # bak de ligger.
                    if (
                        not is_played(match)
                        and kickoff - _now_naive() > timedelta(days=7)
                    ):
                        continue

                    lineups = client.fetch_lineups(match.MatchID)
                    players = get_team_players(lineups, team_id)
                    _save_appearances(db, match.MatchID, players, kickoff)

            # Spara trupp-spelare från lagets Players[]-lista (även de utan matcher)
            squad_at = _now_naive()
            db.flush()  # gör match-fasens pending-objekt synliga för get()
            for squad_player in team.Players:
                _upsert_squad_player(db, squad_player, squad_at)
                _upsert_player_team(db, squad_player.PlayerID, team_label)

            # Lagtillhörighet: unionen av trupp-listan (ovan) och spelade matcher
            db.flush()
            _sync_player_teams(db, team_label)

        log.ok = True

    except Exception as exc:
        warnings.append(f"Synken avbröts med fel: {exc}")

    finally:
        log.matches_added = matches_added
        log.warnings = warnings
        log.finished_at = _now_naive()
        db.commit()

    # Läs ut värden medan sessionen fortfarande är öppen
    return SyncResult(
        ok=log.ok,
        matches_added=log.matches_added,
        warnings=list(log.warnings),
        started_at=log.started_at,
        finished_at=log.finished_at,
        log_id=log.id,
    )


if __name__ == "__main__":
    Base.metadata.create_all(engine)   # säkerhetsnät om alembic inte körts
    client = IBISClient()
    with SessionLocal() as db:
        log = run_sync(db, client)

    status = "klar" if log.ok else "misslyckades"
    print(f"Synk {status}. {log.matches_added} matcher tillagda.")
    for w in log.warnings:
        print(f"Varning: {w}")
    sys.exit(0 if log.ok else 1)
