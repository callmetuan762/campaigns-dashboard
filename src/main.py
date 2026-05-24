"""Application lifecycle for Phase 2: Meta Ads ingestion, scheduled reports, and alerts.

Order (do not reorder -- each step depends on the previous):
    1. load_settings            -- fail fast on missing required env vars
    2. configure_logging        -- every subsequent component logs through this pipeline
    3. db.connect               -- opens aiosqlite, applies migrations
    4. create_bot_and_dispatcher -- Bot + Dispatcher with allowlist registered
    5. delete_webhook           -- avoids Pitfall 6 (409 Conflict)
    6. register_job_resources   -- wire module globals BEFORE scheduler.add_job
    7. AsyncIOScheduler         -- built INSIDE the loop (Pitfall 2)
    8. scheduler.start + dp.start_polling
    9. finally: scheduler.shutdown -> bot.session.close -> db.close
"""
from __future__ import annotations

import asyncio

import structlog
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import src.ga4.ingest as ga4_ingest_module
import src.meta.ingest as meta_ingest_module
import src.mmm.scheduler as mmm_scheduler_module
import src.reports.daily as daily_report_module
import src.reports.weekly as weekly_report_module
from aiogram.types import BotCommand

from src.bot.setup import create_bot_and_dispatcher
from src.config import load_settings
from src.db.client import DBClient
from src.logging_setup import configure_logging


async def main() -> None:
    # 1. Config (fail fast on missing required env)
    settings = load_settings()

    # 1b. Sentry (Phase 5) — conditional on DSN presence; lazy import avoids overhead when absent
    if settings.sentry_dsn:
        import sentry_sdk
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        sentry_sdk.init(
            dsn=settings.sentry_dsn.get_secret_value(),
            integrations=[AsyncioIntegration()],
            environment=settings.sentry_environment,
            traces_sample_rate=0.0,
            send_default_pii=False,
        )

    # 2. Logging (everything below this point logs through redaction)
    configure_logging(level=settings.log_level, fmt="json")
    log = structlog.get_logger(__name__)
    log.info("boot", phase=2, timezone=settings.report_timezone, db_path=str(settings.db_path))

    # 3. Storage
    db = DBClient(settings.db_path)
    await db.connect()
    log.info("storage_ready", path=str(settings.db_path))

    # 4. Bot + Dispatcher (allowlist registered inside the factory)
    bot, dp = create_bot_and_dispatcher(settings, db)

    # 5. Clear any stale webhook so long-polling won't get 409 (Pitfall 6)
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("webhook_cleared")

    # Register the "/" command menu shown in Telegram clients.
    await bot.set_my_commands([
        BotCommand(command="report", description="Generate latest daily report"),
        BotCommand(command="ask",    description="Ask about ad performance — e.g. /ask best ROAS this week?"),
        BotCommand(command="status", description="Show last sync time and row counts"),
        BotCommand(command="clear",  description="Clear your AI conversation history"),
        BotCommand(command="help",   description="Show available commands"),
    ])
    log.info("bot_commands_registered")

    # Phase 2: Register module-level resources for APScheduler jobs.
    # Must be called BEFORE scheduler.add_job() and scheduler.start().
    # CRITICAL: Resources are NOT passed as job args (PicklingError with SQLAlchemyJobStore).
    ga4_ingest_module.register_job_resources(bot, db, settings)
    meta_ingest_module.register_job_resources(bot, db, settings)
    daily_report_module.register_job_resources(bot, db, settings)
    weekly_report_module.register_job_resources(bot, db, settings)
    mmm_scheduler_module.register_job_resources(bot, db, settings)

    # 6. Scheduler (constructed INSIDE the running loop -- Pitfall 2)
    jobstore = SQLAlchemyJobStore(url=f"sqlite:///{settings.db_path}")
    scheduler = AsyncIOScheduler(
        jobstores={"default": jobstore},
        timezone=settings.report_timezone,
    )
    scheduler.add_job(
        ga4_ingest_module.ga4_ingest_job,
        trigger=CronTrigger(hour=1, minute=0, timezone=settings.report_timezone),
        id="ga4_ingest",
        replace_existing=True,
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        meta_ingest_module.meta_ingest_job,
        trigger=CronTrigger(hour=settings.meta_ingest_hour, minute=0, timezone=settings.report_timezone),
        id="meta_ingest",
        replace_existing=True,
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        daily_report_module.daily_report_job,
        trigger=CronTrigger(hour=settings.daily_report_hour, minute=0, timezone=settings.report_timezone),
        id="daily_report",
        replace_existing=True,
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        weekly_report_module.weekly_report_job,
        trigger=CronTrigger(day_of_week="mon", hour=settings.daily_report_hour, minute=0, timezone=settings.report_timezone),
        id="weekly_report",
        replace_existing=True,
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        mmm_scheduler_module.run_mmm_weekly_job,
        trigger=CronTrigger(day_of_week="sun", hour=23, minute=0, timezone=settings.report_timezone),
        id="mmm_weekly",
        replace_existing=True,
        misfire_grace_time=600,
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
