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
POST /api/matches/{id}/roster-edits          – lägg till eller ta bort en spelare i matchens trupp
DELETE /api/matches/{id}/roster-edits/{pid}  – ångra en tidigare ändring
GET  /{path}                  – serverar byggt frontend (SPA-fallback)
"""

import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
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


class ShotEventIn(BaseModel):
    id: str
    player_id: int
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

        deleted_at = _parse_client_ts(h.deleted_at) if h.deleted_at else None
        existing = db.get(ShotEvent, h.id)
        if existing is None:
            db.add(ShotEvent(
                id=h.id,
                match_id=match_id,
                player_id=h.player_id,
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
