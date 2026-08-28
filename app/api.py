"""
FastAPI-endpoints för spelbarhetskoll.

GET  /health                  – hälsokontroll, kräver inte inloggning
GET  /api/status              – beräknad spelbarhetslista grupperad i tre grupper
POST /api/sync                – kör synken manuellt
POST /auth/login              – sätt session-cookie efter lösenordsverifikation
POST /auth/logout             – rensa session-cookie
POST /api/overrides           – skapa eller ersätt override för en spelare
DELETE /api/overrides/{id}    – ta bort override för en spelare
GET  /{path}                  – serverar byggt frontend (SPA-fallback)
"""

import threading
from contextlib import asynccontextmanager
from datetime import datetime
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
from app.ibis_client import IBISClient
from app.models import Appearance, Match, Override, Player, PlayerTeam, SyncLog
from app.status import get_statuses
from app.sync import run_sync

_DIST = Path(__file__).parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    from app.scheduler import start_scheduler
    start_scheduler()
    yield


app = FastAPI(lifespan=lifespan)

_cache_lock = threading.Lock()
_status_cache: dict[str, Any] | None = None

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
        }
        if is_locked:
            locked.append(row)
        elif matcher_kvar == 0:
            must_sit.append(row)
        else:
            available.append(row)

    # Spelare i truppen som inte spelat någon match alls
    for pid, p in players_by_id.items():
        if pid not in seen_ids:
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
def post_sync(
    db: Session = Depends(get_db),
    _: None = Depends(require_session),
) -> dict:
    global _status_cache
    client = IBISClient()
    result = run_sync(db, client)

    new_status = _build_status_response(db)
    with _cache_lock:
        _status_cache = new_status

    return {
        "ok": result.ok,
        "matcher_tillagda": result.matches_added,
        "varningar": result.warnings,
        "startad": result.started_at.isoformat(),
        "klar": result.finished_at.isoformat(),
    }


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

    hall = raw.get("MainVenue") or raw.get("Venue")
    hall = hall.strip() if isinstance(hall, str) and hall.strip() else None

    goals_home = raw.get("GoalsHomeTeam")
    goals_away = raw.get("GoalsAwayTeam")
    resultat = None
    if m.status == "played" and goals_home is not None and goals_away is not None:
        resultat = {"hemma": goals_home, "borta": goals_away}

    return {
        "match_id": m.match_id,
        "kickoff": m.kickoff.isoformat(),
        "motstandare": m.opponent,
        "hemma": hemma,
        "hall": hall,
        "status": m.status,
        "omgang": m.round_name,
        "resultat": resultat,
    }


def _match_detail(db: Session, m: Match) -> dict[str, Any]:
    data = _match_summary(m)
    spelad = m.status == "played"

    apps = db.scalars(
        select(Appearance).where(Appearance.match_id == m.match_id)
    ).all()

    gk_ids: set[int] = set()
    if apps:
        gk_ids = {
            p.player_id
            for p in db.scalars(
                select(Player).where(
                    Player.player_id.in_([a.player_id for a in apps])
                )
            ).all()
            if p.is_goalkeeper
        }

    trupp = [
        {
            "player_id": a.player_id,
            "namn": a.player_name,
            "trojnummer": a.shirt_no,
            "malvakt": a.player_id in gk_ids,
            "mal": a.goals if spelad else None,
            "assist": a.assists if spelad else None,
            "utvisningsminuter": a.penalty_minutes if spelad else None,
        }
        for a in apps
    ]

    def _sort_key(row: dict) -> tuple:
        try:
            nr = int(row["trojnummer"])
        except (TypeError, ValueError):
            nr = 9999
        return (not row["malvakt"], nr, row["namn"] or "")

    trupp.sort(key=_sort_key)

    data["spelad"] = spelad
    data["trupp_publicerad"] = len(trupp) > 0
    data["trupp"] = trupp
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
