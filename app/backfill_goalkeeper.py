"""
Backfill av goalkeeper_id på gamla motståndarskott (SPEC 6.8).

Kör: python -m app.backfill_goalkeeper [--dry-run]

Målvaktsvalet tillkom efter att skottregistreringen tagits i bruk, så alla
motståndarskott som registrerades innan dess har goalkeeper_id = null och
räknas därför inte in i någon målvakts statistik.

Fyller i dem – men bara när det går att göra säkert:

  - Matchens trupp har exakt en målvakt → alla motståndarskott utan målvakt
    tillskrivs honom
  - Matchens trupp har flera målvakter → lämnas null. Vi kan inte veta vem som
    stod, och en gissning vore värre än en saknad siffra
  - Matchens trupp har ingen målvakt → lämnas null
  - Våra egna skott rörs aldrig

Skriptet är idempotent: skott som redan har en målvakt lämnas orörda, så det
går att köra om.
"""

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Appearance, Match, Player, RosterEdit, ShotEvent
from app.roster import apply_roster_edits, roster_edits_for_matches


def _goalkeepers_in_squad(db: Session, match_id: int) -> list[Player]:
    """
    Målvakterna i matchens *effektiva* trupp.

    Effektiv = iBIS-appearances plus roster_edits (SPEC 6.5), alltså exakt den
    trupp matchvyn visar. En spelare som tränaren lagt till manuellt räknas
    därför med, och en borttagen räknas bort.
    """
    apps = db.scalars(
        select(Appearance).where(Appearance.match_id == match_id)
    ).all()
    base = [(match_id, a.player_id, a.player_name) for a in apps]

    edits = roster_edits_for_matches(db, {match_id})
    edit_ids = {e.player_id for e in edits}
    namn = {
        p.player_id: p.name
        for p in db.scalars(
            select(Player).where(Player.player_id.in_(edit_ids))
        ).all()
    } if edit_ids else {}

    effektiva = {pid for (_m, pid, _n) in apply_roster_edits(base, edits, namn)}
    if not effektiva:
        return []

    return list(db.scalars(
        select(Player).where(
            Player.player_id.in_(effektiva),
            Player.is_goalkeeper.is_(True),
        )
    ).all())


def backfill(db: Session, *, dry_run: bool = False) -> dict:
    """
    Fyller i goalkeeper_id där det går. Returnerar en sammanfattning.

    ``tilldelade``  antal skott som fick en målvakt
    ``lamnade``     antal skott som lämnades null
    ``matcher``     per match: (match_id, antal, beslut)
    """
    saknar = db.scalars(
        select(ShotEvent).where(
            ShotEvent.side == "motstandare",
            ShotEvent.goalkeeper_id.is_(None),
        )
    ).all()

    per_match: dict[int, list[ShotEvent]] = {}
    for e in saknar:
        per_match.setdefault(e.match_id, []).append(e)

    tilldelade = 0
    lamnade = 0
    rader: list[tuple[int, int, str]] = []

    for match_id in sorted(per_match):
        skott = per_match[match_id]
        malvakter = _goalkeepers_in_squad(db, match_id)

        if len(malvakter) == 1:
            mv = malvakter[0]
            if not dry_run:
                for e in skott:
                    e.goalkeeper_id = mv.player_id
            tilldelade += len(skott)
            rader.append((
                match_id, len(skott),
                f"tilldelade {mv.name} ({mv.player_id})",
            ))
        else:
            lamnade += len(skott)
            if len(malvakter) == 0:
                beslut = "lämnade – ingen målvakt i truppen"
            else:
                namn = ", ".join(f"{m.name} ({m.player_id})" for m in malvakter)
                beslut = f"lämnade – flera målvakter i truppen: {namn}"
            rader.append((match_id, len(skott), beslut))

    if not dry_run:
        db.commit()

    return {
        "tilldelade": tilldelade,
        "lamnade": lamnade,
        "matcher": rader,
    }


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    with SessionLocal() as db:
        resultat = backfill(db, dry_run=dry_run)

    if dry_run:
        print("Torrkörning – ingenting skrevs till databasen.\n")

    if not resultat["matcher"]:
        print("Inga motståndarskott saknar målvakt. Ingenting att göra.")
        return 0

    for match_id, antal, beslut in resultat["matcher"]:
        ord_skott = "skott" if antal == 1 else "skott"
        print(f"  Match {match_id}: {antal} {ord_skott} – {beslut}")

    print()
    print(f"Tilldelade: {resultat['tilldelade']} skott")
    print(f"Lämnade:    {resultat['lamnade']} skott")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
