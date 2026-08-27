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
    IBISTeam,
    filter_series_competitions,
    get_team_players,
    is_played,
    parse_kickoff,
)
from app.models import Appearance, Base, Match, Player, SyncLog

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
    db: Session, m: IBISMatch, team_label: str, team_id: int, raw: dict
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
            raw=raw,
        )
        db.add(match)
        return match, True

    existing.status = status
    existing.raw = raw
    return existing, False


def _upsert_player(db: Session, p: IBISMatchPlayer, kickoff: datetime) -> None:
    existing = db.get(Player, p.PlayerID)
    if existing is None:
        db.add(Player(
            player_id=p.PlayerID,
            name=p.Name,
            shirt_no=p.ShirtNo,
            last_seen=kickoff,
        ))
    else:
        existing.name = p.Name
        if p.ShirtNo is not None:
            existing.shirt_no = p.ShirtNo
        if kickoff > existing.last_seen:
            existing.last_seen = kickoff


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
        if db.get(Appearance, (match_id, p.PlayerID)) is None:
            db.add(Appearance(
                match_id=match_id,
                player_id=p.PlayerID,
                player_name=p.Name,
                shirt_no=p.ShirtNo,
            ))


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
            raw_team = client.fetch_team_raw(settings.season_id, team_id)
            team = IBISTeam.model_validate(raw_team)

            # Bygg uppslag match_id → rådata för seriematcher
            raw_by_match: dict[int, dict] = {}
            for comp_data in raw_team.get("Competitions", []):
                if comp_data.get("CompetitionTypeID") == 1:
                    for m_data in comp_data.get("Matches", []):
                        raw_by_match[m_data["MatchID"]] = m_data

            for comp in filter_series_competitions(team):
                for match in comp.Matches:
                    raw = raw_by_match.get(match.MatchID, match.model_dump(mode="json"))
                    db_match, is_new = _upsert_match(db, match, team_label, team_id, raw)

                    if is_new:
                        matches_added += 1

                    # Avbruten utan resultat: logga varning, hoppa över appearances
                    if match.Abandoned and not is_played(match):
                        warnings.append(
                            f"Match {match.MatchID} ({match.MatchDateTime[:10]}): "
                            "avbruten utan registrerat resultat – hoppas över"
                        )
                        continue

                    if not is_played(match):
                        continue

                    # Hoppa över om appearances redan finns (färdigrapporterad match)
                    if not is_new and _has_appearances(db, match.MatchID):
                        continue

                    lineups = client.fetch_lineups(match.MatchID)
                    players = get_team_players(lineups, team_id)
                    kickoff = parse_kickoff(match.MatchDateTime).replace(tzinfo=None)
                    _save_appearances(db, match.MatchID, players, kickoff)

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
