"""
Roster edits – ett lager ovanpå iBIS-appearances (SPEC 6.5).

iBIS-datan skrivs aldrig över. En roster_edit ligger ovanpå:
  - 'add'    lägger till en spelare i en matchs underlag
  - 'remove' tar bort en spelare som iBIS registrerat felaktigt

Samma funktion används av regelmotorn (app/status.py) och av matchvyn
(app/api.py) så att en ändring slår igenom på både låsningsberäkningen och
skottregistreringens spelarlista.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RosterEdit


def roster_edits_for_matches(db: Session, match_ids: set[int]) -> list[RosterEdit]:
    """Alla roster_edits för de angivna matcherna, i tidsordning."""
    if not match_ids:
        return []
    return list(
        db.scalars(
            select(RosterEdit)
            .where(RosterEdit.match_id.in_(match_ids))
            .order_by(RosterEdit.created_at, RosterEdit.id)
        ).all()
    )


def apply_roster_edits(
    base: list[tuple[int, int, str]],
    edits: list[RosterEdit],
    player_names: dict[int, str],
) -> list[tuple[int, int, str]]:
    """
    Lägger roster_edits ovanpå iBIS-appearances.

    base: (match_id, player_id, player_name) från iBIS.
    edits: roster_edits, redan filtrerade till relevanta matcher.
    player_names: player_id -> namn, för tillagda spelare som saknar appearance.

    Returnerar den effektiva listan (match_id, player_id, player_name).
    """
    present: dict[tuple[int, int], str] = {(m, p): name for (m, p, name) in base}
    for e in edits:
        key = (e.match_id, e.player_id)
        if e.action == "remove":
            present.pop(key, None)
        elif e.action == "add":
            present[key] = player_names.get(e.player_id, f"Spelare {e.player_id}")
    return [(m, p, name) for (m, p), name in present.items()]
