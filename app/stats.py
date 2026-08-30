"""
Statistik per spelare (SPEC 7 – Del 3).

Aggregerar per spelare, för ett lag och en vald omfattning:

  - Matcher: antal matcher i truppen inom omfattningen
  - Mål, assist, poäng, utvisningsminuter från iBIS-appearances
  - Skott totalt = mål + på mål + utanför + i täck, samt de fyra andelarna
    (summerar alltid till 100)

Bara seriematcher räknas (SPEC 10: cup och träningsmatcher är utanför scope),
och bara spelade matcher – en publicerad men ospelad trupp ger ingen statistik.

Lagseparation sköts av att anroparen alltid anger ett lag: varje match hör till
lag A eller B, så en spelares siffror hör till matchens lag (SPEC 7).

Saknad data: skott finns bara för matcher där någon registrerat. En spelare vars
matcher i omfattningen saknar registrering får tomma skottfält
(``skott.registrerat = False``), aldrig noll – noll skott och ingen registrering
är olika saker. Skottbreddningens "mål" räknas över samma matcher som skotten,
så de fyra andelarna hänger ihop även när bara en del av matcherna är
registrerade.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Appearance, Match, Player, ShotEvent
from app.roster import apply_roster_edits, roster_edits_for_matches

SCOPES = ("senaste", "senaste_n", "sasong")

# Skottkategorierna i visningsordning, med nyckeln som används i svaret.
_SHOT_KEYS = (
    ("mal", None),           # målandelen kommer från iBIS, inte shot_events
    ("pa_mal", "on_goal"),
    ("utanfor", "missed"),
    ("i_tack", "blocked"),
)


def _select_scope(match_ids: list[int], scope: str, n: int) -> list[int]:
    """match_ids är spelade seriematcher för laget, i speldatumordning."""
    if scope == "senaste":
        return match_ids[-1:]
    if scope == "senaste_n":
        return match_ids[-n:] if n > 0 else []
    return list(match_ids)


def _shares(parts: list[int], total: int) -> list[int | None]:
    """
    Fyra heltalsandelar som summerar till exakt 100 (största rest-metoden).
    Returnerar None för alla om det inte finns något att fördela.
    """
    if total <= 0:
        return [None] * len(parts)
    exact = [p * 100 / total for p in parts]
    floors = [int(x) for x in exact]
    left = 100 - sum(floors)
    order = sorted(range(len(parts)), key=lambda i: exact[i] - floors[i], reverse=True)
    for i in order[:left]:
        floors[i] += 1
    return floors


def compute_stats(db: Session, team: str, scope: str, n: int = 5) -> dict:
    matches = db.scalars(
        select(Match)
        .where(
            Match.team == team,
            Match.status == "played",
            Match.counts_for_rules.is_(True),
        )
        .order_by(Match.kickoff, Match.match_id)
    ).all()

    scoped_ids = _select_scope([m.match_id for m in matches], scope, n)
    scoped_set = set(scoped_ids)

    if not scoped_set:
        return {
            "lag": team,
            "omfattning": {"scope": scope, "n": n, "antal_matcher": 0},
            "spelare": [],
        }

    apps = {
        (a.match_id, a.player_id): a
        for a in db.scalars(
            select(Appearance).where(Appearance.match_id.in_(scoped_set))
        ).all()
    }

    player_names = {p.player_id: p.name for p in db.scalars(select(Player)).all()}
    base = [(m, p, apps[(m, p)].player_name) for (m, p) in apps]
    edits = roster_edits_for_matches(db, scoped_set)
    squad = {(m, p) for (m, p, _name) in apply_roster_edits(base, edits, player_names)}

    # Skott: bara aktiva händelser (tombstones räknas inte). Vilka matcher som
    # över huvud taget har en registrering avgör om skottfälten visas.
    shots: dict[tuple[int, int], dict[str, int]] = {}
    registered_matches: set[int] = set()
    for e in db.scalars(
        select(ShotEvent).where(
            ShotEvent.match_id.in_(scoped_set),
            ShotEvent.deleted_at.is_(None),
            # Bara egna skott. Motståndarens skott (SPEC 6.1) hör inte till någon
            # spelare och ska inte markera en match som registrerad här.
            ShotEvent.side == "egen",
        )
    ).all():
        registered_matches.add(e.match_id)
        bucket = shots.setdefault((e.match_id, e.player_id), {})
        bucket[e.kind] = bucket.get(e.kind, 0) + 1

    matches_by_player: dict[int, list[int]] = {}
    for (m, p) in squad:
        matches_by_player.setdefault(p, []).append(m)

    players = {
        p.player_id: p
        for p in db.scalars(
            select(Player).where(Player.player_id.in_(matches_by_player.keys()))
        ).all()
    }

    rader: list[dict] = []
    for pid, mids in matches_by_player.items():
        p = players.get(pid)
        apps_for = [apps[(m, pid)] for m in mids if (m, pid) in apps]

        mal = sum(a.goals for a in apps_for)
        assist = sum(a.assists for a in apps_for)
        utv = sum(a.penalty_minutes for a in apps_for)

        reg_mids = [m for m in mids if m in registered_matches]
        if reg_mids:
            skott_mal = sum(
                apps[(m, pid)].goals for m in reg_mids if (m, pid) in apps
            )
            counts = {"on_goal": 0, "missed": 0, "blocked": 0}
            for m in reg_mids:
                for kind, c in shots.get((m, pid), {}).items():
                    counts[kind] += c

            parts = [
                skott_mal,
                counts["on_goal"],
                counts["missed"],
                counts["blocked"],
            ]
            totalt = sum(parts)
            andelar = _shares(parts, totalt)
            skott = {
                "registrerat": True,
                "totalt": totalt,
                **{
                    key: {"antal": parts[i], "andel": andelar[i]}
                    for i, (key, _wire) in enumerate(_SHOT_KEYS)
                },
            }
        else:
            skott = {"registrerat": False}

        rader.append({
            "player_id": pid,
            "namn": p.name if p else player_names.get(pid, f"Spelare {pid}"),
            "trojnummer": p.shirt_no if p else None,
            "malvakt": bool(p.is_goalkeeper) if p else False,
            "matcher": len(mids),
            "mal": mal,
            "assist": assist,
            "poang": mal + assist,
            "utvisningsminuter": utv,
            "skott": skott,
        })

    rader.sort(key=lambda r: (-r["poang"], -r["mal"], r["namn"] or ""))

    return {
        "lag": team,
        "omfattning": {"scope": scope, "n": n, "antal_matcher": len(scoped_ids)},
        "spelare": rader,
    }
