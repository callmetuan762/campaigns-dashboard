"""Application lifecycle for Phase 1: foundation & walking skeleton.

Order (do not reorder -- each step depends on the previous):
    1. load_settings       -- fail fast on missing required env vars
    2. configure_logging   -- every subsequent component logs through this pipeline
    3. db.connect          -- opens aiosqlite, applies migrations
    4. bot + dispatcher    -- Bot + Dispatcher with allowlist registered
    5. delete_webhook      -- avoids Pitfall 6 (409 Conflict)
    6. AsyncIOScheduler    -- built INSIDE the loop (Pitfall 2)
    7. scheduler.start + dp.start_polling
    8. finally: scheduler.shutdown -> bot.session.close -> db.close
"""
from __future__ import annotations

import asyncio

import structlog
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.bot.setup import create_bot_and_dispatcher
from src.config import load_settings
from src.db.client import DBClient
from src.logging_setup import configure_logging


async def _scheduler_heartbeat() -> None:
    """Phase 1 placeholder job -- proves the scheduler is wired and firing.

    Phase 2 replaces this with the real Meta ingest job; Phase 2/3 add the
    daily digest / weekly summary jobs.
    """
    structlog.get_logger(__name__).info("scheduler_heartbeat")


async def main() -> None:
    # 1. Config (fail fast on missing required env)
    settings = load_settings()

    # 2. Logging (everything below this point logs through redaction)
    configure_logging(level=settings.log_level, fmt="json")
    log = structlog.get_logger(__name__)
    log.info("boot", phase=1, timezone=settings.report_timezone, db_path=str(settings.db_path))

    # 3. Storage
    db = DBClient(settings.db_path)
    await db.connect()
    log.info("storage_ready", path=str(settings.db_path))

    # 4. Bot + Dispatcher (allowlist registered inside the factory)
    bot, dp = create_bot_and_dispatcher(settings, db)

    # 5. Clear any stale webhook so long-polling won't get 409 (Pitfall 6)
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("webhook_cleared")

    # 6. Scheduler (constructed INSIDE the running loop -- Pitfall 2)
    jobstore = SQLAlchemyJobStore(url=f"sqlite:///{settings.db_path}")
    scheduler = AsyncIOScheduler(
        jobstores={"default": jobstore},
        timezone=settings.report_timezone,
    )
    scheduler.add_job(
        _scheduler_heartbeat,
        trigger=CronTrigger(minute="*/15", timezone=settings.report_timezone),
        id="phase1_heartbeat",
        replace_existing=True,
        misfire_grace_time=60,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    log.info("scheduler_started", jobs=len(scheduler.get_jobs()))

    # 7. Long-polling (blocking until SIGINT/SIGTERM)
    try:
        log.info("polling_start")
        await dp.start_polling(bot)
    finally:
        log.info("shutdown_start")
        try:
            scheduler.shutdown(wait=False)
        except Exception as e:  # noqa: BLE001
            log.warning("scheduler_shutdown_error", error=str(e))
        try:
            await bot.session.close()
        except Exception as e:  # noqa: BLE001
            log.warning("bot_close_error", error=str(e))
        await db.close()
        log.info("shutdown_complete")


if __name__ == "__main__":
    asyncio.run(main())
