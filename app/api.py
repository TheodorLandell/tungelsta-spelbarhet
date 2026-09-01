"""
FastAPI-endpoints för spelbarhetskoll.

GET  /health                  – hälsokontroll, kräver inte inloggning
GET  /api/status              – beräknad spelbarhetslista grupperad i tre grupper
POST /api/sync                – kör synken manuellt
POST /auth/login              – sätt session-cookie efter lösenordsverifikation
POST /auth/logout             – rensa session-cookie
POST /api/overrides           – skapa eller ersätt override för en spelare
DELETE /api/overrides/{id}    – ta bort override för en spelare
GET  /api/matches             – matchlista per lag
GET  /api/matches/{id}        – matchvy med trupp
GET  /api/matches/{id}/shot-events   – alla skotthändelser för en match
POST /api/matches/{id}/shot-events   – ta emot en batch skotthändelser (idempotent på klient-UUID)
GET  /api/stats               – statistik per spelare för ett lag och en omfattning
POST /api/matches/{id}/roster-edits          – lägg till eller ta bort en spelare i matchens trupp
DELETE /api/matches/{id}/roster-edits/{pid}  – ångra en tidigare ändring
GET  /{path}                  – serverar byggt frontend (SPA-fallback)
"""

import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    create_session_token,
    require_session,
    verify_password,
)
from app.config import settings
from app.database import SessionLocal
from app.ibis_client import (
    IBISClient,
    IBISMatch,
    get_team_players,
    is_played,
)
from app.models import (
    Appearance,
    Match,
    Override,
    Player,
    PlayerTeam,
    RosterEdit,
    ShotEvent,
    SyncLog,
)
from app.stats import SCOPES, compute_stats
from app.status import get_statuses
from app.sync import _match_status, _now_naive, run_sync

_DIST = Path(__file__).parent.parent / "frontend" / "dist"

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    from app.scheduler import start_scheduler
    start_scheduler()
    yield


app = FastAPI(lifespan=lifespan)

_cache_lock = threading.Lock()
_status_cache: dict[str, Any] | None = None

# Live-uppdatering av matchresultatet (SPEC 6.6). Svaret cachas ~30 sekunder på
# servern, så att flera klienter som pollar samtidigt bara ger ett anrop mot
# iBIS. refreshing hindrar att två samtidiga förfrågningar båda ringer iBIS.
LIVE_TTL_SECONDS = 30
LIVE_WINDOW_HOURS = 4
LIVE_IBIS_TIMEOUT = 6.0

_live_lock = threading.Lock()
_live_state: dict[str, Any] = {"ts": None, "payload": None, "refreshing": False}

# POST /api/sync startar synken i en bakgrundstråd och svarar direkt (SPEC
# 3.5): lineups hämtas sekventiellt med paus och kan ta minuter, så ett
# synkront anrop skulle hinna timeouta. _sync_state["running"] hindrar att två
# synkar körs samtidigt – startar någon en synk medan en redan pågår svarar
# endpointen att en redan är igång.
_sync_state_lock = threading.Lock()
_sync_state: dict[str, bool] = {"running": False}

_COOKIE_MAX_AGE = 30 * 24 * 3600


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _clear_status_cache() -> None:
    global _status_cache
    with _cache_lock:
        _status_cache = None


def _clear_live_cache() -> None:
    with _live_lock:
        _live_state["ts"] = None
        _live_state["payload"] = None
        _live_state["refreshing"] = False


def _sync_worker() -> None:
    """Kör synken och bygger om statuscachen. Körs i en bakgrundstråd så att
    POST /api/sync kan svara direkt utan att hålla HTTP-anropet öppet."""
    global _status_cache
    try:
        client = IBISClient()
        with SessionLocal() as db:
            run_sync(db, client)
            new_status = _build_status_response(db)
        with _cache_lock:
            _status_cache = new_status
    except Exception:
        log.exception("Bakgrundssynken avbröts med ett oväntat fel")
    finally:
        with _sync_state_lock:
            _sync_state["running"] = False


def _start_background_sync() -> bool:
    """Startar en bakgrundssynk om ingen redan pågår. Returnerar True om en ny
    synk startades, False om en redan var igång."""
    with _sync_state_lock:
        if _sync_state["running"]:
            return False
        _sync_state["running"] = True
    threading.Thread(target=_sync_worker, name="sync-worker", daemon=True).start()
    return True


def _last_sync_ts(db: Session) -> str | None:
    log = db.scalars(
        select(SyncLog)
        .where(SyncLog.ok.is_(True))
        .order_by(SyncLog.finished_at.desc())
        .limit(1)
    ).first()
    return log.finished_at.isoformat() if log else None


def _latest_played_kickoff(db: Session) -> datetime | None:
    m = db.scalars(
        select(Match)
        .where(Match.status == "played")
        .order_by(Match.kickoff.desc())
        .limit(1)
    ).first()
    return m.kickoff if m else None


def _override_info(o: Override, latest_kickoff: datetime | None) -> dict:
    stale = latest_kickoff is not None and latest_kickoff > o.data_snapshot
    return {
        "id": o.id,
        "kind": o.kind,
        "value": o.value,
        "note": o.note,
        "created_at": o.created_at.isoformat(),
        "stale": stale,
    }


def _roster_edit_info(edits: list[RosterEdit]) -> dict:
    """Sammanfattning för del 1: senaste ändringen plus antal om det är flera."""
    latest = max(edits, key=lambda e: (e.created_at, e.id))
    return {
        "action": latest.action,
        "note": latest.note,
        "created_at": latest.created_at.isoformat(),
        "antal": len(edits),
    }


def _build_status_response(db: Session) -> dict[str, Any]:
    statuses, engine_warnings = get_statuses(db)
    players_by_id = {p.player_id: p for p in db.scalars(select(Player)).all()}

    teams_by_player: dict[int, list[str]] = {}
    for pt in db.scalars(select(PlayerTeam)).all():
        teams_by_player.setdefault(pt.player_id, []).append(pt.team)

    active_overrides: dict[int, Override] = {
        o.player_id: o for o in db.scalars(select(Override)).all()
    }
    latest_kickoff = _latest_played_kickoff(db)

    # Roster edits på matcher som räknas i reglerna. En sådan ändring kan bara
    # påverka den redigerade spelaren själv: kedjan räknas per spelare, och att
    # lägga till eller ta bort en spelare i en trupp rör ingen annans
    # appearances. En edit på en match med counts_for_rules = False finns inte
    # bland counting_match_ids och kan därför aldrig markera någon.
    counting_match_ids = {
        m.match_id
        for m in db.scalars(select(Match).where(Match.counts_for_rules.is_(True))).all()
    }
    roster_edits_by_player: dict[int, list[RosterEdit]] = {}
    for e in db.scalars(select(RosterEdit)).all():
        if e.match_id in counting_match_ids:
            roster_edits_by_player.setdefault(e.player_id, []).append(e)

    roster_affected: set[int] = set()
    if roster_edits_by_player:
        raw_statuses, _ = get_statuses(db, apply_edits=False)

        def _sig(src: dict[int, Any], pid: int):
            s = src.get(pid)
            return (
                None
                if s is None
                else (s.locked, s.lock_reason, s.matches_left, s.consecutive_a)
            )

        for pid in roster_edits_by_player:
            if _sig(statuses, pid) != _sig(raw_statuses, pid):
                roster_affected.add(pid)

    def _roster_cell(pid: int) -> dict | None:
        if pid not in roster_affected:
            return None
        return _roster_edit_info(roster_edits_by_player[pid])

    must_sit: list[dict] = []
    available: list[dict] = []
    locked: list[dict] = []
    seen_ids: set[int] = set()

    for s in statuses.values():
        seen_ids.add(s.player_id)
        p = players_by_id.get(s.player_id)

        is_locked = s.locked
        matcher_kvar = s.matches_left
        lock_orsak = s.lock_reason.value if s.lock_reason else None
        lock_datum = s.lock_date.strftime("%Y-%m-%d") if s.lock_date else None

        ovr = active_overrides.get(s.player_id)
        if ovr is not None:
            if ovr.kind == "lock":
                is_locked = True
                lock_orsak = None
                lock_datum = None
            elif ovr.kind == "unlock":
                is_locked = False
                lock_orsak = None
                lock_datum = None
                ca = s.consecutive_a or 0
                matcher_kvar = max(0, 2 - ca)
            elif ovr.kind == "set_matches_left":
                is_locked = False
                lock_orsak = None
                lock_datum = None
                matcher_kvar = ovr.value

        row: dict[str, Any] = {
            "player_id": s.player_id,
            "namn": s.player_name,
            "trojnummer": p.shirt_no if p else None,
            "matcher_kvar": matcher_kvar,
            "consecutive_a": s.consecutive_a,
            "a_match_ids": list(s.a_match_ids),
            "b_match_ids": list(s.b_match_ids),
            "lag": sorted(teams_by_player.get(s.player_id, [])),
            "maste_spela_b_forst": False,
            "lock_orsak": lock_orsak,
            "lock_datum": lock_datum,
            "override": _override_info(ovr, latest_kickoff) if ovr else None,
            "roster_edit": _roster_cell(s.player_id),
        }
        if is_locked:
            locked.append(row)
        elif matcher_kvar == 0:
            must_sit.append(row)
        else:
            available.append(row)

    # Listan tar bara med spelare som stått i truppen i en spelad seriematch
    # (de finns då i statuses ovan) – inte hela den registrerade truppen
    # (SPEC 5). Undantaget är en spelare med en aktiv override: den är en
    # medveten åtgärd av tränaren och ska synas även utan spelad match.
    for pid, p in players_by_id.items():
        if pid not in seen_ids and pid in active_overrides:
            ovr = active_overrides.get(pid)
            matcher_kvar = 2
            if ovr is not None and ovr.kind == "set_matches_left":
                matcher_kvar = ovr.value

            row = {
                "player_id": pid,
                "namn": p.name,
                "trojnummer": p.shirt_no,
                "matcher_kvar": matcher_kvar,
                "consecutive_a": 0,
                "a_match_ids": [],
                "b_match_ids": [],
                "lag": sorted(teams_by_player.get(pid, [])),
                "maste_spela_b_forst": True,
                "lock_orsak": None,
                "lock_datum": None,
                "override": _override_info(ovr, latest_kickoff) if ovr else None,
                "roster_edit": _roster_cell(pid),
            }
            if ovr is not None and ovr.kind == "lock":
                locked.append(row)
            elif matcher_kvar == 0:
                must_sit.append(row)
            else:
                available.append(row)

    must_sit.sort(key=lambda r: r["namn"])
    available.sort(key=lambda r: (r["matcher_kvar"] or 0, r["namn"]))
    locked.sort(key=lambda r: r["namn"])

    return {
        "senaste_sync": _last_sync_ts(db),
        "varningar": engine_warnings,
        "grupper": {
            "maste_sta_over": must_sit,
            "tillgangliga": available,
            "lasta": locked,
        },
        "rakningar": {
            "maste_sta_over": len(must_sit),
            "tillgangliga": len(available),
            "lasta": len(locked),
        },
    }


def _filter_response_by_team(response: dict[str, Any], team: str) -> dict[str, Any]:
    """
    Filtrerar spelarlistan på lagtillhörighet (player_teams). Ett filter, inte
    en behörighetsspärr: varningar och senaste synk lämnas orörda.
    """
    grupper = {
        namn: [r for r in rader if team in r["lag"]]
        for namn, rader in response["grupper"].items()
    }
    return {
        **response,
        "grupper": grupper,
        "rakningar": {namn: len(rader) for namn, rader in grupper.items()},
    }


# ---------------------------------------------------------------------------
# Health (ingen auth)
# ---------------------------------------------------------------------------

@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"ok": True}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class LoginBody(BaseModel):
    password: str


@app.post("/auth/login")
def post_login(body: LoginBody, response: Response) -> dict:
    if not verify_password(body.password):
        raise HTTPException(status_code=401, detail="Fel lösenord")
    token = create_session_token()
    response.set_cookie(
        key="session",
        value=token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


@app.post("/auth/logout")
def post_logout(response: Response) -> dict:
    response.delete_cookie("session")
    return {"ok": True}


# ---------------------------------------------------------------------------
# API (kräver giltig session)
# ---------------------------------------------------------------------------

@app.get("/api/status")
def get_status(
    team: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_session),
) -> dict:
    if team is not None and team not in ("A", "B"):
        raise HTTPException(status_code=400, detail="team måste vara A eller B")

    global _status_cache
    with _cache_lock:
        if _status_cache is None:
            _status_cache = _build_status_response(db)
        full = _status_cache

    return _filter_response_by_team(full, team) if team else full


@app.post("/api/sync")
def post_sync(_: None = Depends(require_session)) -> dict:
    """
    Startar synken i bakgrunden och svarar direkt, så anropet aldrig hinner
    timeouta medan lineups hämtas sekventiellt (SPEC 3.5). Pågår redan en synk
    startas ingen ny – svaret säger då att en redan är igång.
    """
    startad = _start_background_sync()
    return {"startad": startad, "pagar": True}


@app.get("/api/sync/status")
def get_sync_status(
    db: Session = Depends(get_db),
    _: None = Depends(require_session),
) -> dict:
    """Talar om ifall en synk pågår och sammanfattar den senaste körningen, så
    frontend vet när den bakgrundskörda synken blivit klar."""
    with _sync_state_lock:
        pagar = _sync_state["running"]

    log_row = db.scalars(
        select(SyncLog).order_by(SyncLog.started_at.desc()).limit(1)
    ).first()
    senaste = None
    if log_row is not None:
        senaste = {
            "startad": log_row.started_at.isoformat(),
            "klar": log_row.finished_at.isoformat() if log_row.finished_at else None,
            "ok": log_row.ok,
            "matcher_tillagda": log_row.matches_added,
            "varningar": list(log_row.warnings or []),
        }
    return {"pagar": pagar, "senaste": senaste}


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------

class OverrideBody(BaseModel):
    player_id: int
    kind: str
    value: int | None = None
    note: str


@app.post("/api/overrides")
def post_override(
    body: OverrideBody,
    db: Session = Depends(get_db),
    _: None = Depends(require_session),
) -> dict:
    if body.kind not in ("lock", "unlock", "set_matches_left"):
        raise HTTPException(status_code=400, detail="Ogiltig kind")
    if body.kind == "set_matches_left" and body.value not in (0, 1, 2):
        raise HTTPException(status_code=400, detail="value måste vara 0, 1 eller 2")

    value = body.value if body.kind == "set_matches_left" else None
    snapshot = _latest_played_kickoff(db) or datetime.now()

    for o in db.scalars(select(Override).where(Override.player_id == body.player_id)).all():
        db.delete(o)

    db.add(Override(
        player_id=body.player_id,
        kind=body.kind,
        value=value,
        note=body.note,
        created_at=datetime.now(),
        created_by="admin",
        data_snapshot=snapshot,
    ))
    db.commit()
    _clear_status_cache()
    return {"ok": True}


@app.delete("/api/overrides/{player_id}")
def delete_override(
    player_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_session),
) -> dict:
    for o in db.scalars(select(Override).where(Override.player_id == player_id)).all():
        db.delete(o)
    db.commit()
    _clear_status_cache()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Matchlista och matchvy (skrivskyddad – steg 12)
# ---------------------------------------------------------------------------

def _team_id_for(team_label: str) -> int:
    return settings.team_a_id if team_label == "A" else settings.team_b_id


def _match_summary(m: Match) -> dict[str, Any]:
    raw = m.raw or {}

    home_id = raw.get("HomeTeamID")
    hemma = None if home_id is None else home_id == _team_id_for(m.team)

    def _clean(v: Any) -> str | None:
        return v.strip() if isinstance(v, str) and v.strip() else None

    hemmalag = _clean(raw.get("HomeTeam"))
    bortalag = _clean(raw.get("AwayTeam"))

    hall = raw.get("MainVenue") or raw.get("Venue")
    hall = hall.strip() if isinstance(hall, str) and hall.strip() else None

    goals_home = raw.get("GoalsHomeTeam")
    goals_away = raw.get("GoalsAwayTeam")
    resultat = None
    if m.status == "played" and goals_home is not None and goals_away is not None:
        resultat = {"hemma": goals_home, "borta": goals_away}

    # Matcher som inte räknas i reglerna markeras i listan (SPEC 1, 6.1)
    if m.counts_for_rules:
        matchtyp = "serie"
    elif raw.get("CompetitionTypeID") == 3:
        matchtyp = "cup"
    else:
        matchtyp = "traningsmatch"

    return {
        "match_id": m.match_id,
        "kickoff": m.kickoff.isoformat(),
        "motstandare": m.opponent,
        "hemma": hemma,
        "hemmalag": hemmalag,
        "bortalag": bortalag,
        "hall": hall,
        "status": m.status,
        "omgang": m.round_name,
        "resultat": resultat,
        "raknas": m.counts_for_rules,
        "matchtyp": matchtyp,
    }


def _roster_edit_cell(e: RosterEdit) -> dict[str, Any]:
    return {
        "action": e.action,
        "note": e.note,
        "created_at": e.created_at.isoformat(),
        "created_by": e.created_by,
    }


def _match_detail(db: Session, m: Match) -> dict[str, Any]:
    data = _match_summary(m)
    spelad = m.status == "played"

    apps = {
        a.player_id: a
        for a in db.scalars(
            select(Appearance).where(Appearance.match_id == m.match_id)
        ).all()
    }

    # Roster edits ligger som ett lager ovanpå iBIS (SPEC 6.5). En borttagen
    # spelare försvinner ur listan, en tillagd läggs till. Slår igenom på både
    # skottregistreringen (den här listan) och regelmotorn.
    edits = {
        e.player_id: e
        for e in db.scalars(
            select(RosterEdit)
            .where(RosterEdit.match_id == m.match_id)
            .order_by(RosterEdit.created_at, RosterEdit.id)
        ).all()
    }
    removed_ids = {pid for pid, e in edits.items() if e.action == "remove"}
    added_ids = {pid for pid, e in edits.items() if e.action == "add"}
    effective_ids = (set(apps) - removed_ids) | added_ids

    need_ids = effective_ids | removed_ids
    players_by_id = (
        {
            p.player_id: p
            for p in db.scalars(
                select(Player).where(Player.player_id.in_(need_ids))
            ).all()
        }
        if need_ids
        else {}
    )

    def _name(pid: int) -> str:
        a = apps.get(pid)
        if a is not None:
            return a.player_name
        p = players_by_id.get(pid)
        return p.name if p is not None else f"Spelare {pid}"

    def _shirt(pid: int) -> str | None:
        a = apps.get(pid)
        if a is not None:
            return a.shirt_no
        p = players_by_id.get(pid)
        return p.shirt_no if p is not None else None

    def _is_gk(pid: int) -> bool:
        p = players_by_id.get(pid)
        return bool(p.is_goalkeeper) if p is not None else False

    trupp = []
    for pid in effective_ids:
        a = apps.get(pid)  # None för en tillagd spelare utan iBIS-appearance
        e = edits.get(pid)
        trupp.append({
            "player_id": pid,
            "namn": _name(pid),
            "trojnummer": _shirt(pid),
            "malvakt": _is_gk(pid),
            "mal": a.goals if (spelad and a is not None) else None,
            "assist": a.assists if (spelad and a is not None) else None,
            "utvisningsminuter": (
                a.penalty_minutes if (spelad and a is not None) else None
            ),
            "roster_edit": _roster_edit_cell(e) if e is not None else None,
        })

    def _sort_key(row: dict) -> tuple:
        try:
            nr = int(row["trojnummer"])
        except (TypeError, ValueError):
            nr = 9999
        return (not row["malvakt"], nr, row["namn"] or "")

    trupp.sort(key=_sort_key)

    # Borttagna spelare visas separat i redigeringsläget med en återställ-knapp.
    borttagna = sorted(
        (
            {
                "player_id": pid,
                "namn": _name(pid),
                "trojnummer": _shirt(pid),
                "malvakt": _is_gk(pid),
                "roster_edit": _roster_edit_cell(edits[pid]),
            }
            for pid in removed_ids
        ),
        key=lambda r: r["namn"] or "",
    )

    # Kandidater att lägga till: lagets trupp som inte redan finns i matchens.
    team_player_ids = db.scalars(
        select(PlayerTeam.player_id).where(PlayerTeam.team == m.team)
    ).all()
    lagtrupp = sorted(
        (
            {
                "player_id": p.player_id,
                "namn": p.name,
                "trojnummer": p.shirt_no,
                "malvakt": bool(p.is_goalkeeper),
            }
            for p in db.scalars(
                select(Player).where(Player.player_id.in_(team_player_ids))
            ).all()
            if p.player_id not in effective_ids
        ),
        key=lambda r: r["namn"] or "",
    )

    # Lagmål till matchhuvudet (SPEC 6.2). Motståndarens mål hämtas från iBIS
    # precis som våra egna, inte manuellt. null tills iBIS synkats.
    resultat = data["resultat"]
    if resultat is not None and data["hemma"] is not None:
        if data["hemma"]:
            data["mal"], data["motstandare_mal"] = resultat["hemma"], resultat["borta"]
        else:
            data["mal"], data["motstandare_mal"] = resultat["borta"], resultat["hemma"]
    else:
        data["mal"] = data["motstandare_mal"] = None

    data["spelad"] = spelad
    data["trupp_publicerad"] = len(trupp) > 0
    data["trupp"] = trupp
    data["borttagna"] = borttagna
    data["lagtrupp"] = lagtrupp
    return data


@app.get("/api/matches")
def get_matches(
    team: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_session),
) -> dict:
    if team not in ("A", "B"):
        raise HTTPException(status_code=400, detail="team måste vara A eller B")

    rows = db.scalars(
        select(Match).where(Match.team == team).order_by(Match.kickoff)
    ).all()
    return {"matcher": [_match_summary(m) for m in rows]}


@app.get("/api/matches/{match_id}")
def get_match(
    match_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_session),
) -> dict:
    m = db.get(Match, match_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Matchen finns inte")
    return _match_detail(db, m)


# ---------------------------------------------------------------------------
# Live-uppdatering av matchresultatet (SPEC 6.6)
#
# Hemmalaget rapporterar mål löpande i iBIS under matchen. GET /api/live hämtar
# de pågående matchernas resultat och spelarstatistik – inte hela synken. Svaret
# cachas ~30 sekunder på servern, och rör aldrig regelmotorns cache: spelbarhets-
# och statistikvyn uppdateras fortfarande bara via Uppdatera-knappen och
# nattjobbet. Endpointen skriver inget till databasen.
#
# Pågående match = kickoff har passerat, matchen är inte färdigrapporterad
# (FinalResultCreatedTS saknas) och det är högst ~4 timmar sedan kickoff. Är
# ingen match pågående svaras tomt utan att iBIS anropas alls.
# ---------------------------------------------------------------------------

def _ongoing_matches(db: Session) -> list[Match]:
    now = _now_naive()
    lower = now - timedelta(hours=LIVE_WINDOW_HOURS)
    rows = db.scalars(
        select(Match).where(
            Match.status != "cancelled",
            Match.kickoff <= now,
            Match.kickoff >= lower,
        )
    ).all()
    return [m for m in rows if not (m.raw or {}).get("FinalResultCreatedTS")]


def _live_match_row(client: IBISClient, m: Match, md: dict, team_id: int) -> dict:
    ibis_match = IBISMatch.model_validate(md)
    played = is_played(ibis_match)

    home_id = md.get("HomeTeamID")
    hemma = None if home_id is None else home_id == team_id

    goals_home = md.get("GoalsHomeTeam")
    goals_away = md.get("GoalsAwayTeam")
    resultat = None
    if played and goals_home is not None and goals_away is not None:
        resultat = {"hemma": goals_home, "borta": goals_away}

    if resultat is not None and hemma is not None:
        mal, motstandare_mal = (
            (resultat["hemma"], resultat["borta"])
            if hemma
            else (resultat["borta"], resultat["hemma"])
        )
    else:
        mal = motstandare_mal = None

    spelare: list[dict] = []
    if played:
        try:
            lineups = client.fetch_lineups(m.match_id)
            for p in get_team_players(lineups, team_id):
                spelare.append({
                    "player_id": p.PlayerID,
                    "mal": p.Goals or 0,
                    "assist": p.Assists or 0,
                    "utvisningsminuter": p.PenaltyMinutes or 0,
                })
        except ValueError:
            # Laget finns inte i lineupen än – ta med resultatet ändå.
            pass

    return {
        "match_id": m.match_id,
        "status": _match_status(ibis_match),
        "hemma": hemma,
        "mal": mal,
        "motstandare_mal": motstandare_mal,
        "resultat": resultat,
        "spelare": spelare,
    }


def _refresh_live(db: Session, matches: list[Match]) -> dict:
    client = IBISClient(timeout=LIVE_IBIS_TIMEOUT, max_retries=1)

    by_team: dict[str, list[Match]] = {}
    for m in matches:
        by_team.setdefault(m.team, []).append(m)

    rader: list[dict] = []
    for team_label, team_matches in by_team.items():
        team_id = _team_id_for(team_label)
        raw_team = client.fetch_team_raw(settings.season_id, team_id)
        idx: dict[int, dict] = {}
        for comp in raw_team.get("Competitions", []):
            for md in comp.get("Matches", []):
                idx[md["MatchID"]] = md
        for m in team_matches:
            md = idx.get(m.match_id)
            if md is not None:
                rader.append(_live_match_row(client, m, md, team_id))

    return {"hamtad": _now_naive().isoformat(), "matcher": rader}


@app.get("/api/live")
def get_live(
    db: Session = Depends(get_db),
    _: None = Depends(require_session),
) -> dict:
    ongoing = _ongoing_matches(db)
    if not ongoing:
        return {"hamtad": _now_naive().isoformat(), "matcher": []}

    now = time.monotonic()
    with _live_lock:
        payload = _live_state["payload"]
        ts = _live_state["ts"]
        if payload is not None and ts is not None and now - ts < LIVE_TTL_SECONDS:
            return payload
        if _live_state["refreshing"]:
            # Någon annan förfrågan hämtar redan – ge det senaste vi har hellre
            # än att ringa iBIS en gång till. Tomt om vi inte hämtat än.
            return payload or {"hamtad": None, "matcher": []}
        _live_state["refreshing"] = True

    try:
        fresh = _refresh_live(db, ongoing)
    except Exception:
        with _live_lock:
            stale = _live_state["payload"]
            _live_state["refreshing"] = False
        if stale is not None:
            return stale
        raise HTTPException(status_code=502, detail="Kunde inte nå iBIS")
    else:
        with _live_lock:
            _live_state["payload"] = fresh
            _live_state["ts"] = time.monotonic()
            _live_state["refreshing"] = False
        return fresh


# ---------------------------------------------------------------------------
# Ändra matchlista (steg 15) – SPEC 6.5
#
# roster_edits ligger som ett lager ovanpå iBIS. iBIS-datan skrivs aldrig över.
# Ändringen påverkar både regelmotorn och skottregistreringens spelarlista.
# Högst en aktiv rad per (match, spelare) – en ny ersätter en tidigare. Att
# ångra en ändring raderar raden och återställer läget till iBIS.
# ---------------------------------------------------------------------------

_ROSTER_ACTIONS = ("add", "remove")


class RosterEditBody(BaseModel):
    player_id: int
    action: str
    note: str
    created_by: str | None = None


@app.post("/api/matches/{match_id}/roster-edits")
def post_roster_edit(
    match_id: int,
    body: RosterEditBody,
    db: Session = Depends(get_db),
    _: None = Depends(require_session),
) -> dict:
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Matchen finns inte")
    if body.action not in _ROSTER_ACTIONS:
        raise HTTPException(status_code=400, detail="action måste vara add eller remove")
    note = body.note.strip()
    if not note:
        raise HTTPException(status_code=400, detail="Anteckning krävs")
    if db.get(Player, body.player_id) is None:
        raise HTTPException(status_code=404, detail="Spelaren finns inte")

    for e in db.scalars(
        select(RosterEdit).where(
            RosterEdit.match_id == match_id,
            RosterEdit.player_id == body.player_id,
        )
    ).all():
        db.delete(e)

    db.add(RosterEdit(
        match_id=match_id,
        player_id=body.player_id,
        action=body.action,
        note=note,
        created_at=datetime.now(),
        created_by=(body.created_by or "").strip() or "admin",
    ))
    db.commit()
    _clear_status_cache()
    return {"ok": True}


@app.delete("/api/matches/{match_id}/roster-edits/{player_id}")
def delete_roster_edit(
    match_id: int,
    player_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_session),
) -> dict:
    for e in db.scalars(
        select(RosterEdit).where(
            RosterEdit.match_id == match_id,
            RosterEdit.player_id == player_id,
        )
    ).all():
        db.delete(e)
    db.commit()
    _clear_status_cache()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Skottsynk (steg 14) – SPEC 6.3 och 6.4
#
# Klientens UUID är primärnyckel, så en batch kan skickas om hur många gånger
# som helst utan att bli dubbletter. Tombstones (deleted_at) skickas som vanliga
# händelser – ingenting raderas någonsin.
# ---------------------------------------------------------------------------

_SHOT_KINDS = ("on_goal", "missed", "blocked")
_SHOT_SIDES = ("egen", "motstandare")


class ShotEventIn(BaseModel):
    id: str
    # null för motståndarens skott – de registreras bara på lagnivå (SPEC 6.1)
    player_id: int | None = None
    side: str = "egen"
    kind: str
    period: int
    created_at: str
    created_by: str | None = None
    deleted_at: str | None = None


class ShotEventBatch(BaseModel):
    handelser: list[ShotEventIn]


def _parse_client_ts(value: str) -> datetime:
    """Klienten skickar ISO-tid i UTC (Date.toISOString). Lagras naivt i UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _shot_event_out(e: ShotEvent) -> dict[str, Any]:
    return {
        "id": e.id,
        "match_id": e.match_id,
        "player_id": e.player_id,
        "side": e.side,
        "kind": e.kind,
        "period": e.period,
        "created_at": e.created_at.isoformat() + "Z",
        "created_by": e.created_by,
        "deleted_at": e.deleted_at.isoformat() + "Z" if e.deleted_at else None,
    }


@app.get("/api/matches/{match_id}/shot-events")
def get_shot_events(
    match_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_session),
) -> dict:
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Matchen finns inte")

    rows = db.scalars(
        select(ShotEvent)
        .where(ShotEvent.match_id == match_id)
        .order_by(ShotEvent.created_at)
    ).all()
    return {"handelser": [_shot_event_out(e) for e in rows]}


@app.post("/api/matches/{match_id}/shot-events")
def post_shot_events(
    match_id: int,
    body: ShotEventBatch,
    db: Session = Depends(get_db),
    _: None = Depends(require_session),
) -> dict:
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Matchen finns inte")

    sparade: list[str] = []
    for h in body.handelser:
        if not h.id:
            raise HTTPException(status_code=422, detail="Händelse saknar id")
        if h.kind not in _SHOT_KINDS:
            raise HTTPException(status_code=422, detail=f"Ogiltig kategori: {h.kind}")
        if h.period not in (1, 2, 3):
            raise HTTPException(status_code=422, detail=f"Ogiltig period: {h.period}")
        if h.side not in _SHOT_SIDES:
            raise HTTPException(status_code=422, detail=f"Ogiltig sida: {h.side}")
        # Egna skott hör till en spelare; motståndarens registreras bara på
        # lagnivå och har därför ingen spelare (SPEC 6.1).
        if h.side == "egen" and h.player_id is None:
            raise HTTPException(status_code=422, detail="Eget skott saknar player_id")
        player_id = h.player_id if h.side == "egen" else None

        deleted_at = _parse_client_ts(h.deleted_at) if h.deleted_at else None
        existing = db.get(ShotEvent, h.id)
        if existing is None:
            db.add(ShotEvent(
                id=h.id,
                match_id=match_id,
                player_id=player_id,
                side=h.side,
                kind=h.kind,
                period=h.period,
                created_at=_parse_client_ts(h.created_at),
                created_by=(h.created_by or None),
                deleted_at=deleted_at,
            ))
        elif deleted_at is not None and existing.deleted_at is None:
            # Samma händelse igen – det enda som kan ha ändrats är tombstonen.
            existing.deleted_at = deleted_at

        sparade.append(h.id)

    db.commit()
    return {"sparade": sparade, "antal": len(sparade)}


# ---------------------------------------------------------------------------
# Statistik (steg 17) – SPEC 7
#
# Per spelare, för valt lag och vald omfattning. Lagseparationen ligger i att
# team alltid anges: en spelare som spelat i båda lagen får sina A-siffror i
# team=A och sina B-siffror i team=B.
#
#   scope=senaste     senaste spelade seriematchen
#   scope=senaste_n   de senaste n spelade seriematcherna (n default 5)
#   scope=sasong      hela säsongen
# ---------------------------------------------------------------------------

@app.get("/api/stats")
def get_stats(
    team: str,
    scope: str = "sasong",
    n: int = 5,
    db: Session = Depends(get_db),
    _: None = Depends(require_session),
) -> dict:
    if team not in ("A", "B"):
        raise HTTPException(status_code=400, detail="team måste vara A eller B")
    if scope not in SCOPES:
        raise HTTPException(status_code=400, detail="Ogiltig omfattning")
    if scope == "senaste_n" and n < 1:
        raise HTTPException(status_code=400, detail="n måste vara minst 1")

    return compute_stats(db, team, scope, n)


# ---------------------------------------------------------------------------
# Statiska filer och SPA-fallback (måste vara sist)
# ---------------------------------------------------------------------------

@app.get("/{full_path:path}", include_in_schema=False)
def spa_catchall(full_path: str) -> FileResponse:
    if not _DIST.exists():
        raise HTTPException(status_code=404, detail="Frontend ej byggd")
    candidate = _DIST / full_path if full_path else _DIST / "index.html"
    if candidate.is_file():
        return FileResponse(str(candidate))
    return FileResponse(str(_DIST / "index.html"))
