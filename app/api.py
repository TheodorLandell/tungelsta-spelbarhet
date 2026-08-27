"""
FastAPI-endpoints för spelbarhetskoll.

GET  /api/status  – beräknad spelbarhetslista grupperad i tre grupper
POST /api/sync    – kör synken manuellt
"""

import threading
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.ibis_client import IBISClient
from app.models import Player, SyncLog
from app.status import get_statuses
from app.sync import run_sync

app = FastAPI()

_cache_lock = threading.Lock()
_status_cache: dict[str, Any] | None = None


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


def _build_status_response(db: Session) -> dict[str, Any]:
    statuses, engine_warnings = get_statuses(db)
    players_by_id = {p.player_id: p for p in db.scalars(select(Player)).all()}

    must_sit: list[dict] = []
    available: list[dict] = []
    locked: list[dict] = []
    seen_ids: set[int] = set()

    for s in statuses.values():
        seen_ids.add(s.player_id)
        p = players_by_id.get(s.player_id)
        row: dict[str, Any] = {
            "player_id": s.player_id,
            "namn": s.player_name,
            "trojnummer": p.shirt_no if p else None,
            "matcher_kvar": s.matches_left,
            "consecutive_a": s.consecutive_a,
            "a_match_ids": list(s.a_match_ids),
            "b_match_ids": list(s.b_match_ids),
            "maste_spela_b_forst": False,
            "lock_orsak": s.lock_reason.value if s.lock_reason else None,
            "lock_datum": s.lock_date.strftime("%Y-%m-%d") if s.lock_date else None,
        }
        if s.locked:
            locked.append(row)
        elif s.matches_left == 0:
            must_sit.append(row)
        else:
            available.append(row)

    # Spelare i truppen som inte spelat någon match alls
    for pid, p in players_by_id.items():
        if pid not in seen_ids:
            available.append({
                "player_id": pid,
                "namn": p.name,
                "trojnummer": p.shirt_no,
                "matcher_kvar": 2,
                "consecutive_a": 0,
                "a_match_ids": [],
                "b_match_ids": [],
                "maste_spela_b_forst": True,
                "lock_orsak": None,
                "lock_datum": None,
            })

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


@app.get("/api/status")
def get_status(db: Session = Depends(get_db)) -> dict:
    global _status_cache
    with _cache_lock:
        if _status_cache is not None:
            return _status_cache
        _status_cache = _build_status_response(db)
        return _status_cache


@app.post("/api/sync")
def post_sync(db: Session = Depends(get_db)) -> dict:
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
