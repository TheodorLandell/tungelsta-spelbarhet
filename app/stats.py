"""
Statistik per spelare (SPEC 7 – Del 3).

Aggregerar per spelare, för ett lag och en vald omfattning:

  - Matcher: antal matcher i truppen inom omfattningen
  - Mål, assist, poäng, utvisningsminuter från iBIS-appearances
  - Skott totalt = på mål + utanför + i täck, samt de tre andelarna
    (summerar alltid till 100)

Ett mål är ett skott på mål. Mål registreras inte manuellt (SPEC 6.2), så
``pa_mal`` är de registrerade skotten på mål **plus** målen från iBIS. Målen
ingår därmed i totalen via på mål och räknas aldrig en gång till som en egen
post – annars skulle varje mål räknas dubbelt.

Mål visas fortfarande som eget värde, tillsammans med målprocenten: mål delat
med skott på mål.

Bara seriematcher räknas (SPEC 10: cup och träningsmatcher är utanför scope),
och bara spelade matcher – en publicerad men ospelad trupp ger ingen statistik.

Lagseparation sköts av att anroparen alltid anger ett lag: varje match hör till
lag A eller B, så en spelares siffror hör till matchens lag (SPEC 7).

Saknad data: skott finns bara för matcher där någon registrerat. En spelare vars
matcher i omfattningen saknar registrering får tomma skottfält
(``skott.registrerat = False``), aldrig noll – noll skott och ingen registrering
är olika saker. Målen som räknas in i ``pa_mal`` räknas över samma matcher som
skotten, så andelarna hänger ihop även när bara en del av matcherna är
registrerade.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.goalkeeper import attribute_conceded, save_stats
from app.models import Appearance, Match, Player, ShotEvent
from app.periods import match_goal_split
from app.roster import apply_roster_edits, roster_edits_for_matches

SCOPES = ("senaste", "senaste_n", "sasong")

# Skottkategorierna i visningsordning, med nyckeln som används i svaret.
# De tre är hela totalen och summerar till 100 %. Mål ligger inne i "pa_mal"
# och är alltså ingen egen post här.
_SHOT_KEYS = (
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
    Heltalsandelar som summerar till exakt 100 (största rest-metoden).
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


def _goalkeeper_totals(
    db: Session, team: str, scoped_set: set[int], matches: list[Match]
) -> dict:
    """
    Summerar målvaktssiffror över omfattningen.

    Returnerar ``{"registrerade": {gk: skott på mål mot}, "inslappta": {gk: mål},
    "sedda": set, "approximativ": bool, "oattribuerat": int}``. ``sedda`` är de
    målvakter som faktiskt har registrerade motståndarskott – bara de får
    siffror, resten visas tomma (SPEC 6.8).
    """
    registrerade: dict[int, int] = {}
    inslappta: dict[int, int] = {}
    sedda: set[int] = set()
    approximativ = False
    oattribuerat = 0

    if not scoped_set:
        return {
            "registrerade": registrerade, "inslappta": inslappta,
            "sedda": sedda, "approximativ": approximativ,
            "oattribuerat": oattribuerat,
        }

    # match_id -> period -> goalkeeper_id -> antal motståndarskott
    per_match: dict[int, dict[int, dict[int, int]]] = {}
    for e in db.scalars(
        select(ShotEvent).where(
            ShotEvent.match_id.in_(scoped_set),
            ShotEvent.deleted_at.is_(None),
            ShotEvent.side == "motstandare",
        )
    ).all():
        # Skott registrerade innan målvaktsvalet fanns saknar målvakt och
        # räknas inte in någonstans – hellre saknad siffra än gissad.
        if e.goalkeeper_id is None:
            continue
        sedda.add(e.goalkeeper_id)
        bucket = per_match.setdefault(e.match_id, {}).setdefault(e.period, {})
        bucket[e.goalkeeper_id] = bucket.get(e.goalkeeper_id, 0) + 1
        if e.kind == "on_goal":
            registrerade[e.goalkeeper_id] = registrerade.get(e.goalkeeper_id, 0) + 1

    team_id = settings.team_a_id if team == "A" else settings.team_b_id

    for m in matches:
        if m.match_id not in scoped_set:
            continue
        events_by_period = per_match.get(m.match_id)
        if not events_by_period:
            continue

        perioder, _total, utan = match_goal_split(
            m.raw or {}, team_id, egen=False
        )
        per_gk, oattr, approx = attribute_conceded(
            events_by_period, perioder or {}, utan
        )
        for gk_id, mal in per_gk.items():
            inslappta[gk_id] = inslappta.get(gk_id, 0) + mal
        oattribuerat += oattr
        approximativ = approximativ or approx

    return {
        "registrerade": registrerade, "inslappta": inslappta,
        "sedda": sedda, "approximativ": approximativ,
        "oattribuerat": oattribuerat,
    }


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

    # Målvaktsstatistik (SPEC 6.8). Motståndarens skott bär själva vilken
    # målvakt som stod, medan insläppta mål bara har period och därför
    # tilldelas per period.
    gk = _goalkeeper_totals(db, team, scoped_set, matches)

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

            # Ett mål är ett skott på mål: målen läggs till de registrerade
            # skotten på mål och räknas aldrig separat i totalen.
            pa_mal = counts["on_goal"] + skott_mal
            parts = [pa_mal, counts["missed"], counts["blocked"]]
            totalt = sum(parts)
            andelar = _shares(parts, totalt)
            skott = {
                "registrerat": True,
                "totalt": totalt,
                "mal": skott_mal,
                # Målprocent = mål delat med skott på mål. None när inget skott
                # på mål finns att dela med.
                "malprocent": (
                    round(skott_mal * 100 / pa_mal) if pa_mal > 0 else None
                ),
                **{
                    key: {"antal": parts[i], "andel": andelar[i]}
                    for i, (key, _wire) in enumerate(_SHOT_KEYS)
                },
            }
        else:
            skott = {"registrerat": False}

        ar_malvakt = bool(p.is_goalkeeper) if p else False

        # Målvakter mäts på motståndarens skott, inte på sina egna (SPEC 6.8).
        # Utan registrerade motståndarskott visas tomt, aldrig noll.
        if not ar_malvakt:
            malvaktsstatistik = None
        elif pid in gk["sedda"]:
            malvaktsstatistik = {
                "registrerat": True,
                **save_stats(
                    gk["registrerade"].get(pid, 0), gk["inslappta"].get(pid, 0)
                ),
                # Byttes målvakt mitt i en period bygger insläppta mål på vem
                # som mötte flest skott. Det ska framgå i UI.
                "approximativ": gk["approximativ"],
            }
        else:
            malvaktsstatistik = {"registrerat": False}

        rader.append({
            "player_id": pid,
            "namn": p.name if p else player_names.get(pid, f"Spelare {pid}"),
            "trojnummer": p.shirt_no if p else None,
            "malvakt": ar_malvakt,
            "matcher": len(mids),
            "mal": mal,
            "assist": assist,
            "poang": mal + assist,
            "utvisningsminuter": utv,
            "skott": skott,
            "malvaktsstatistik": malvaktsstatistik,
        })

    rader.sort(key=lambda r: (-r["poang"], -r["mal"], r["namn"] or ""))

    return {
        "lag": team,
        "omfattning": {"scope": scope, "n": n, "antal_matcher": len(scoped_ids)},
        "spelare": rader,
    }
