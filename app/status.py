"""
Eligibility-tjänst: läser databasen, kör regelmotorn och returnerar spelbarhetsstatus.

Kör manuellt: python -m app.status
"""

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from eligibility import (
    Appearance as EligAppearance,
    Match as EligMatch,
    MatchStatus as EligMatchStatus,
    PlayerStatus,
    Team,
    available_for_b,
    blocked_for_b,
    compute_statuses,
)
from app.database import SessionLocal, engine
from app.models import Appearance as OrmAppearance
from app.models import Base, Match as OrmMatch, Player as OrmPlayer


def get_statuses(db: Session) -> tuple[dict[int, PlayerStatus], list[str]]:
    """
    Läser matcher och appearances ur databasen och kör regelmotorn.

    Endast matcher med counts_for_rules == True skickas in. Cup- och
    träningsmatcher (och deras appearances) filtreras bort helt – de får aldrig
    påverka kvalificeringsregeln eller kedjeregeln.
    """
    orm_matches = db.scalars(
        select(OrmMatch).where(OrmMatch.counts_for_rules.is_(True))
    ).all()
    counting_ids = {m.match_id for m in orm_matches}
    orm_appearances = [
        a
        for a in db.scalars(select(OrmAppearance)).all()
        if a.match_id in counting_ids
    ]

    elig_matches = [
        EligMatch(
            match_id=m.match_id,
            team=Team(m.team),
            kickoff=m.kickoff,
            status=EligMatchStatus(m.status),
        )
        for m in orm_matches
    ]

    elig_appearances = [
        EligAppearance(
            match_id=a.match_id,
            player_id=a.player_id,
            player_name=a.player_name,
        )
        for a in orm_appearances
    ]

    return compute_statuses(elig_matches, elig_appearances)


def _print_table(
    statuses: dict[int, PlayerStatus],
    warnings: list[str],
    shirt_nos: dict[int, str | None] | None = None,
) -> None:
    shirt_nos = shirt_nos or {}

    if not statuses:
        print("Inga spelare i databasen – kör synken först.")
        return

    must_sit = [s for s in statuses.values() if not s.locked and s.matches_left == 0]
    available = [s for s in statuses.values() if not s.locked and (s.matches_left or 0) > 0]
    locked = [s for s in statuses.values() if s.locked]

    col_w = {"nr": 4, "namn": 30, "kvar": 4, "datum": 10, "orsak": 30}
    header = (
        f"  {'Nr':>{col_w['nr']}}  {'Namn':<{col_w['namn']}}"
        f"  {'Kvar':>{col_w['kvar']}}  {'Låst sedan':<{col_w['datum']}}  Orsak"
    )
    sep = "  " + "-" * (col_w["nr"] + col_w["namn"] + col_w["kvar"] + col_w["datum"] + col_w["orsak"] + 10)

    def fmt_row(s: PlayerStatus) -> str:
        nr = shirt_nos.get(s.player_id) or ""
        kvar = str(s.matches_left) if s.matches_left is not None else "-"
        datum = s.lock_date.strftime("%Y-%m-%d") if s.lock_date else ""
        orsak = s.lock_reason.value if s.lock_reason else ""
        return (
            f"  {nr:>{col_w['nr']}}  {s.player_name:<{col_w['namn']}}"
            f"  {kvar:>{col_w['kvar']}}  {datum:<{col_w['datum']}}  {orsak}"
        )

    def section(title: str, players: list[PlayerStatus]) -> None:
        print(f"\n{title} ({len(players)})")
        print(header)
        print(sep)
        if players:
            for s in players:
                print(fmt_row(s))
        else:
            print("  (inga)")

    if must_sit:
        section("MÅSTE STÅ ÖVER NÄSTA A-MATCH", must_sit)
    section("TILLGÄNGLIGA", sorted(available, key=lambda s: (s.matches_left or 0, s.player_name)))
    if locked:
        section("LÅSTA I A-LAGET", sorted(locked, key=lambda s: s.player_name))

    if warnings:
        print(f"\nVARNINGAR ({len(warnings)})")
        for w in warnings:
            print(f"  {w}")


if __name__ == "__main__":
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        statuses, warnings = get_statuses(db)
        shirt_nos = {
            p.player_id: p.shirt_no
            for p in db.scalars(select(OrmPlayer)).all()
        }
    _print_table(statuses, warnings, shirt_nos)
