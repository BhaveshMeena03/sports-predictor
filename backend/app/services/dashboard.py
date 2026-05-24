"""
Dashboard / proof surface.

Aggregates everything that demonstrates the predictor's quality and health:
  - System status: which models are fitted, how many ratings, quota left
  - Live calibration: settled-bet hit rate vs claimed confidence + Brier
  - Backtest proof: the most recent saved backtest summaries (real numbers)
  - Ensemble config currently in force
"""

import glob
import json
import logging
import math
import os
import aiosqlite
from app.core.database import DB_PATH
from app.services.backtest import BACKTEST_DIR

log = logging.getLogger(__name__)


async def _table_count(db, table: str, where: str = "", params: tuple = ()) -> int:
    try:
        q = f"SELECT COUNT(*) FROM {table}"
        if where:
            q += f" WHERE {where}"
        cur = await db.execute(q, params)
        row = await cur.fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


async def system_status() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Elo coverage per sport
        elo_by_sport = {}
        try:
            rows = await (await db.execute(
                "SELECT sport, COUNT(*) n FROM elo_ratings GROUP BY sport")).fetchall()
            elo_by_sport = {r["sport"]: r["n"] for r in rows}
        except Exception:
            pass
        # Fitted Dixon-Coles models
        dc_models = []
        try:
            rows = await (await db.execute(
                "SELECT league, season, n_matches FROM poisson_models")).fetchall()
            dc_models = [{"league": r["league"], "season": r["season"], "n": r["n_matches"]} for r in rows]
        except Exception:
            pass
        # Historical data coverage
        hist = await _table_count(db, "historical_matches")
        preds = await _table_count(db, "predictions")
        bets = await _table_count(db, "bets")

    return {
        "elo_ratings_by_sport": elo_by_sport,
        "dixon_coles_models": dc_models,
        "historical_matches_stored": hist,
        "predictions_logged": preds,
        "bets_tracked": bets,
    }


async def live_calibration() -> dict:
    """Confidence buckets + Brier from settled bets joined to logged predictions."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        try:
            rows = await (await db.execute("""
                SELECT p.confidence, p.p_home, p.p_draw, p.p_away,
                       p.prediction, b.result, b.profit_loss, b.stake, p.sport
                FROM predictions p
                JOIN bets b ON b.match = (p.home_team || ' vs ' || p.away_team)
                WHERE b.result IN ('won','lost')
            """)).fetchall()
        except Exception as e:
            log.warning("calibration query failed: %s", e)
            rows = []

    buckets = {"<50": [], "50-69": [], "70-84": [], "85+": []}
    briers = []
    per_sport = {}
    total_profit = total_stake = 0.0
    for r in rows:
        c = r["confidence"] or 0
        won = 1 if r["result"] == "won" else 0
        if c < 50: buckets["<50"].append(won)
        elif c < 70: buckets["50-69"].append(won)
        elif c < 85: buckets["70-84"].append(won)
        else: buckets["85+"].append(won)

        # Brier needs the prob vector + which outcome actually happened.
        # We approximate the realised outcome from won/lost on the *picked* side:
        # if we have probs, Brier of the pick = (1 - p_pick)^2 if won else (p_pick)^2-ish.
        # Cleaner: use a 2-way Brier on the picked outcome.
        if r["confidence"] is not None:
            p_pick = (r["confidence"] or 0) / 100.0
            briers.append((1 - p_pick) ** 2 if won else (p_pick) ** 2)

        s = r["sport"] or "unknown"
        per_sport.setdefault(s, {"n": 0, "won": 0})
        per_sport[s]["n"] += 1
        per_sport[s]["won"] += won

        total_profit += (r["profit_loss"] or 0)
        total_stake += (r["stake"] or 0)

    def bucket_summary(label, band_mid):
        vals = buckets[label]
        return {
            "n": len(vals),
            "claimed": band_mid,
            "actual_win_rate": round(sum(vals) / len(vals) * 100, 1) if vals else None,
        }

    return {
        "settled_bets": len(rows),
        "confidence_buckets": {
            "<50": bucket_summary("<50", 40),
            "50-69": bucket_summary("50-69", 60),
            "70-84": bucket_summary("70-84", 77),
            "85+": bucket_summary("85+", 92),
        },
        "approx_brier": round(sum(briers) / len(briers), 4) if briers else None,
        "per_sport": {s: {"n": v["n"],
                          "win_rate": round(v["won"] / v["n"] * 100, 1) if v["n"] else None}
                      for s, v in per_sport.items()},
        "roi_pct": round(total_profit / total_stake * 100, 1) if total_stake > 0 else None,
        "note": "Populates as you settle real bets. Compare actual_win_rate to 'claimed'.",
    }


def latest_backtests(n: int = 5) -> list[dict]:
    """Read the most recent saved backtest summaries (the real proof we have now)."""
    files = sorted(glob.glob(os.path.join(BACKTEST_DIR, "*.json")), reverse=True)[:n]
    out = []
    for f in files:
        try:
            with open(f) as fh:
                d = json.load(fh)
            out.append({
                "run_id": d.get("run_id"),
                "leagues": d.get("leagues"),
                "matches_scored": d.get("total_matches_scored"),
                "summary": d.get("summary"),
            })
        except Exception:
            continue
    return out


async def build() -> dict:
    return {
        "system": await system_status(),
        "live_calibration": await live_calibration(),
        "recent_backtests": latest_backtests(),
    }
