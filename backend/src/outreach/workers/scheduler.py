"""APScheduler wiring. Started + stopped from main.py lifespan."""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from outreach.config import get_settings
from outreach.workers import dispatcher, imap_poller, recovery, watchdog

log = logging.getLogger("outreach.scheduler")

_scheduler: AsyncIOScheduler | None = None


def start() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    settings = get_settings()
    if not settings.workers_enabled:
        log.warning("workers disabled by WORKERS_ENABLED=false")
        return
    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(
        dispatcher.tick,
        "interval",
        seconds=settings.tick_interval_seconds,
        id="tick",
        max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        recovery.sweep,
        "interval",
        seconds=settings.recovery_interval_seconds,
        id="recovery",
        max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        imap_poller.poll_all,
        "interval",
        seconds=settings.imap_poll_interval_seconds,
        id="imap_poll",
        max_instances=1,
        coalesce=True,
    )
    # Watchdog patrol tiers — modelled after watchlink's 5-tier daemon.
    sched.add_job(watchdog.quick_check,        "interval", minutes=5,  id="wd_quick",   max_instances=1, coalesce=True)
    sched.add_job(watchdog.channel_patrol,     "interval", minutes=30, id="wd_channel", max_instances=1, coalesce=True)
    sched.add_job(watchdog.stuck_state_sweep,  "interval", hours=1,    id="wd_stuck",   max_instances=1, coalesce=True)
    sched.add_job(watchdog.deep_verify,        "interval", hours=6,    id="wd_deep",    max_instances=1, coalesce=True)
    sched.add_job(watchdog.daily_reset,        "interval", hours=24,   id="wd_daily",   max_instances=1, coalesce=True)
    sched.start()
    _scheduler = sched
    log.info(
        "scheduler started — tick every %ds, recovery every %ds, imap every %ds",
        settings.tick_interval_seconds,
        settings.recovery_interval_seconds,
        settings.imap_poll_interval_seconds,
    )


def stop() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
