"""
Background scheduler — keeps the models fresh without manual intervention.

Uses APScheduler (already in requirements.txt). Jobs:
  - daily model refresh: re-ingest international results + refit Dixon-Coles for
    every league that already has a saved model. Keeps ratings current as new
    matches are played (crucial during a live tournament).

Designed to be safe: jobs catch their own exceptions and never crash the app.
Manual trigger available via /api/scheduler/run-now.
"""

import logging
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_last_run: dict = {"refresh_models": None}


async def refresh_models() -> dict:
    """Re-ingest international results and refit any saved Dixon-Coles models."""
    summary = {"started": datetime.now(timezone.utc).isoformat(), "steps": {}}

    # 1) Refresh international Elo from latest results
    try:
        from app.services.worldcup import ingest_real_results
        r = await ingest_real_results(reset_to_seeds=True)
        summary["steps"]["international_ingest"] = r.get("matches_ingested", "n/a")
    except Exception as e:
        log.warning("scheduled intl ingest failed: %s", e)
        summary["steps"]["international_ingest"] = f"error: {e}"

    # 2) Refit Dixon-Coles for leagues that already have a saved model
    try:
        import aiosqlite
        from app.core.database import DB_PATH
        from app.core.config import settings
        from app.services.poisson import DixonColes, save_model
        from app.services.backtest import fetch_finished_fixtures

        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute(
                "SELECT DISTINCT league, season FROM poisson_models")).fetchall()
        refit = []
        for league, season in rows:
            try:
                fixtures = await fetch_finished_fixtures(league, season=season, limit=500)
                matches, teams = [], set()
                for fx in fixtures:
                    h = fx["teams"]["home"]["name"]; a = fx["teams"]["away"]["name"]
                    hg = fx["goals"]["home"]; ag = fx["goals"]["away"]
                    if hg is None or ag is None:
                        continue
                    matches.append({"home": h, "away": a, "home_goals": hg, "away_goals": ag})
                    teams.add(h); teams.add(a)
                if len(matches) >= 50:
                    m = DixonColes(sorted(teams))
                    m.fit(matches)
                    await save_model("football", league, season, m, len(matches))
                    refit.append(f"{league}({len(matches)})")
            except Exception as e:
                log.warning("scheduled DC refit failed for %s: %s", league, e)
        summary["steps"]["dixon_coles_refit"] = refit
    except Exception as e:
        log.warning("scheduled DC refit step failed: %s", e)
        summary["steps"]["dixon_coles_refit"] = f"error: {e}"

    summary["finished"] = datetime.now(timezone.utc).isoformat()
    _last_run["refresh_models"] = summary
    log.info("scheduled refresh_models complete: %s", summary["steps"])
    return summary


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler(timezone="UTC")
    # Daily at 05:00 UTC (after most fixtures worldwide have finished)
    _scheduler.add_job(refresh_models, "cron", hour=5, minute=0, id="refresh_models",
                       max_instances=1, coalesce=True)
    _scheduler.start()
    log.info("Scheduler started: refresh_models daily @ 05:00 UTC")


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def status() -> dict:
    if not _scheduler:
        return {"running": False, "jobs": [], "last_run": _last_run}
    jobs = [{"id": j.id, "next_run": str(j.next_run_time)} for j in _scheduler.get_jobs()]
    return {"running": True, "jobs": jobs, "last_run": _last_run}
