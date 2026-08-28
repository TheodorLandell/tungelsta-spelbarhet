"""
Schemalagt synkjobb som körs en gång per dygn kl 03:00 Europe/Stockholm.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)


def _daily_sync() -> None:
    from app.database import SessionLocal
    from app.ibis_client import IBISClient
    from app.sync import run_sync

    log.info("Schemalagt synkjobb startar")
    try:
        with SessionLocal() as db:
            result = run_sync(db, IBISClient())
        status = "klar" if result.ok else "misslyckades"
        log.info("Schemalagt synkjobb %s: %d matcher tillagda", status, result.matches_added)
        for w in result.warnings:
            log.warning("Synk-varning: %s", w)
    except Exception:
        log.exception("Schemalagt synkjobb avbröts med oväntat fel")


def start_scheduler() -> None:
    scheduler = BackgroundScheduler(timezone="Europe/Stockholm")
    scheduler.add_job(
        _daily_sync,
        CronTrigger(hour=3, minute=0, timezone="Europe/Stockholm"),
    )
    scheduler.start()
    log.info("Schemaläggare startad (daglig synk kl 03:00 Europe/Stockholm)")
