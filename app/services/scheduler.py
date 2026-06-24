"""Scheduler — APScheduler with SQLAlchemy job store."""
import json
import os
import time
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import DATABASE_URL, settings_cache

SCHEDULER_TIMEZONE = os.getenv("TIMEZONE", "Europe/London")

# Convert async DB URL to sync driver for APScheduler's SQLAlchemyJobStore
_sync_db_url = DATABASE_URL

if _sync_db_url.startswith("postgres://"):
    _sync_db_url = _sync_db_url.replace(
        "postgres://",
        "postgresql+psycopg2://",
        1
    )
elif _sync_db_url.startswith("postgresql+asyncpg://"):
    _sync_db_url = _sync_db_url.replace(
        "postgresql+asyncpg://",
        "postgresql+psycopg2://",
        1
    )
elif _sync_db_url.startswith("postgresql://"):
    _sync_db_url = _sync_db_url.replace(
        "postgresql://",
        "postgresql+psycopg2://",
        1
    )
else:
    # SQLite: replace aiosqlite with sync sqlite driver
    _sync_db_url = _sync_db_url.replace("sqlite+aiosqlite:///", "sqlite:///").replace("sqlite+aiosqlite://", "sqlite://")

jobstores = {
    "default": SQLAlchemyJobStore(url=_sync_db_url)
}

scheduler = AsyncIOScheduler(jobstores=jobstores, timezone=SCHEDULER_TIMEZONE)


async def scrape_job():
    """Nightly full scrape — called by scheduler."""
    from app.services.scraper_orchestrator import run_full_scrape
    return await run_full_scrape()


async def email_job():
    """Morning email digest — called by scheduler."""
    from app.services.email_service import send_digest_email
    await send_digest_email()


async def monitored_job():
    """Periodic monitored-URL check."""
    from app.services.scraper_orchestrator import run_monitored_scrape
    return await run_monitored_scrape()


def _get_email_cron():
    t = settings_cache.get("email_time", "08:00")
    try:
        h, m = t.strip().split(":")
        return {"hour": int(h), "minute": int(m)}
    except (ValueError, AttributeError):
        return {"hour": 8, "minute": 0}


def _get_scrape_cron():
    try:
        h = int(settings_cache.get("scrape_hour", "2"))
    except (ValueError, TypeError):
        logger.warning("Invalid scrape_hour setting — defaulting to 2")
        h = 2
    return {"hour": h, "minute": 0}


def setup_scheduler():
    """Add scheduled jobs. Safe to call multiple times — APScheduler ignores duplicates."""
    jobs = [
        ("full_scrape", scrape_job, CronTrigger(**_get_scrape_cron(), timezone=SCHEDULER_TIMEZONE)),
        ("email_digest", email_job, CronTrigger(**_get_email_cron(), timezone=SCHEDULER_TIMEZONE)),
        ("monitored_check", monitored_job, CronTrigger(hour="*/4", timezone=SCHEDULER_TIMEZONE)),
    ]
    for job_id, func, trigger in jobs:
        existing = scheduler.get_job(job_id)
        if existing is None:
            scheduler.add_job(func, trigger, id=job_id, replace_existing=True,
                               misfire_grace_time=3600)


import asyncio
import logging

logger = logging.getLogger("varshini.scheduler")

# Tracks the currently-running manual scrape so the UI can poll progress
_scrape_state: dict = {
    "running": False, "which": None, "started_at": None,
    "last_result": None, "stage": None,
}
_bg_tasks: set = set()


def get_scrape_state() -> dict:
    return dict(_scrape_state)


def set_scrape_stage(message: str) -> None:
    """Update the plain-language status shown to the user during a scrape."""
    _scrape_state["stage"] = message


async def _run_scrape(which: str):
    start = time.monotonic()
    try:
        if which == "full":
            result = await scrape_job()
        elif which == "instagram":
            from app.services.scraper_orchestrator import run_instagram_scrape
            result = await run_instagram_scrape()
        elif which == "monitored":
            result = await monitored_job()
        else:
            result = {"error": f"Unknown scraper: {which}"}
        elapsed = round(time.monotonic() - start, 1)
        _scrape_state["last_result"] = {"status": "completed", "duration_seconds": elapsed, **(result or {})}
        logger.info(f"Manual scrape ({which}) done in {elapsed}s: {result}")
    except Exception as e:
        logger.exception("Manual scrape failed")
        _scrape_state["last_result"] = {"status": "failed", "error": str(e)}
    finally:
        _scrape_state["running"] = False
        _scrape_state["stage"] = None


async def trigger_scrape_now(which: str = "full") -> dict:
    """Kick off a scrape in the background and return immediately."""
    if which not in ("full", "instagram", "monitored"):
        return {"status": "error", "message": f"Unknown scraper: {which}"}
    if _scrape_state["running"]:
        return {"status": "already_running", "which": _scrape_state["which"]}

    _scrape_state.update(running=True, which=which,
                         started_at=datetime.now().isoformat(), last_result=None,
                         stage="Getting started…")
    task = asyncio.create_task(_run_scrape(which))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return {"status": "started", "which": which}
