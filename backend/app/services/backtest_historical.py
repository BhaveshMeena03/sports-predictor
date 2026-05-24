"""
Historical backtest using football-data.co.uk (results + closing odds).

This is the rigorous backtest: more data (multi-season) and REAL closing odds,
so we can measure ROI — not just calibration. It's self-contained (uses
football-data team names throughout), separate from the live API-Football world.

Pipelines:
  naive          : 1/3, 1/3, 1/3
  elo            : Elo fit on seed window
  poisson        : Dixon-Coles fit on seed window
  market         : de-vigged Pinnacle closing odds (the benchmark to beat)
  ensemble_math  : blend(elo, poisson) — our INDEPENDENT view (for value detection)
  ensemble_market: blend(elo, poisson, market) — best raw calibration

ROI logic:
  - flat: bet 1 unit on each pick at its closing odds
  - value: bet 1 unit ONLY when our model prob > market implied prob + threshold
           (this is the real test — can we beat the closing line?)
"""

import logging
import math
import time
from datetime import datetime

from app.services.elo import EloRatings
from app.services.poisson import DixonColes
from app.services.ensemble import blend
from app.services.market import implied_probs, expected_value
from app.services.historical_data import get_matches
from app.services.backtest import brier, log_loss, actual_outcome_vector, pick_from_probs, BACKTEST_DIR

log = logging.getLogger(__name__)

# In-historical-backtest team-strength tools are fit fresh each run; we use a
# dedicated Elo "sport" namespace so we never collide with live ratings.
HIST_ELO_SPORT = "_hist_football"


def _result_to_vec(result: str) -> list[float]:
    return {"H": [1, 0, 0], "D": [0, 1, 0], "A": [0, 0, 1]}.get(result, [0, 0, 0])


async def _reset_hist_elo():
    import aiosqlite
    from app.core.database import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS elo_ratings ("
                         "sport TEXT, team TEXT, rating REAL, matches INTEGER, updated_at TEXT, "
                         "PRIMARY KEY (sport, team))")
        await db.execute("DELETE FROM elo_ratings WHERE sport=?", (HIST_ELO_SPORT,))
        await db.commit()


async def run_historical_backtest(
    league: str,
    seasons: list[str] = None,
    score_fraction: float = 0.3,
    value_threshold: float = 0.02,
) -> dict:
    """Backtest on stored football-data matches with ROI."""
    started = time.time()
    matches = await get_matches(league=league, seasons=seasons, chronological=True)
    matches = [m for m in matches if m.get("result") in ("H", "D", "A")
               and m.get("home_goals") is not None]
    if len(matches) < 50:
        return {"error": f"Only {len(matches)} usable matches for {league}. Download more seasons first."}

    n_score = max(1, int(len(matches) * score_fraction))
    n_seed = len(matches) - n_score
    seed, score = matches[:n_seed], matches[n_seed:]

    # ─── Fit models on seed window ───
    await _reset_hist_elo()
    elo = EloRatings(HIST_ELO_SPORT)
    for m in seed:
        await elo.update_after_match(m["home_team"], m["away_team"], m["home_goals"], m["away_goals"])

    teams = sorted({m["home_team"] for m in seed} | {m["away_team"] for m in seed})
    dc = DixonColes(teams)
    dc_ok = True
    try:
        dc.fit([{"home": m["home_team"], "away": m["away_team"],
                 "home_goals": m["home_goals"], "away_goals": m["away_goals"]} for m in seed])
    except Exception as e:
        log.warning("historical DC fit failed: %s", e)
        dc_ok = False

    # ─── Score window ───
    pipelines = ["naive", "elo", "poisson", "market", "ensemble_math", "ensemble_market"]
    metrics = {p: {"brier": [], "log_loss": [], "correct": [],
                   "roi_flat": [], "roi_value": [], "value_bets": 0} for p in pipelines}
    ENS_W = {"elo": 0.5, "poisson": 0.5}
    ENS_MKT_W = {"elo": 0.25, "poisson": 0.25, "market": 0.5}

    for m in score:
        actual = _result_to_vec(m["result"])
        odds = [m.get("ps_close_h") or m.get("avg_close_h"),
                m.get("ps_close_d") or m.get("avg_close_d"),
                m.get("ps_close_a") or m.get("avg_close_a")]

        preds = {"naive": [1/3, 1/3, 1/3]}

        ep = await elo.predict_1x2(m["home_team"], m["away_team"])
        preds["elo"] = [ep["p_home"], ep["p_draw"], ep["p_away"]]

        if dc_ok:
            dp = dc.predict_1x2(m["home_team"], m["away_team"])
            preds["poisson"] = [dp["p_home"], dp["p_draw"], dp["p_away"]] if dp else [1/3, 1/3, 1/3]
        else:
            preds["poisson"] = [1/3, 1/3, 1/3]

        mk = implied_probs(*odds) if all(odds) else None
        preds["market"] = [mk["p_home"], mk["p_draw"], mk["p_away"]] if mk else [1/3, 1/3, 1/3]

        preds["ensemble_math"] = blend(
            {"elo": preds["elo"], "poisson": preds["poisson"]}, ENS_W, shrinkage=0.05)
        preds["ensemble_market"] = blend(
            {"elo": preds["elo"], "poisson": preds["poisson"], "market": preds["market"]},
            ENS_MKT_W, shrinkage=0.0)

        # Update Elo online for next match (no leakage — this match is now "past")
        await elo.update_after_match(m["home_team"], m["away_team"], m["home_goals"], m["away_goals"])

        for p in pipelines:
            probs = preds[p]
            metrics[p]["brier"].append(brier(probs, actual))
            metrics[p]["log_loss"].append(log_loss(probs, actual))
            pick = pick_from_probs(probs)
            won = (pick == pick_from_probs(actual))
            metrics[p]["correct"].append(won)

            # ROI needs odds for the picked outcome
            if all(odds):
                pick_odds = odds[pick]
                # Flat: bet the pick every time
                metrics[p]["roi_flat"].append((pick_odds - 1) if won else -1)
                # Value: only bet if our prob beats market-implied by threshold
                market_p = 1.0 / pick_odds
                if probs[pick] > market_p + value_threshold:
                    metrics[p]["value_bets"] += 1
                    metrics[p]["roi_value"].append((pick_odds - 1) if won else -1)

    # ─── Aggregate ───
    def agg(p):
        b = metrics[p]
        n = len(b["brier"])
        roi_flat = sum(b["roi_flat"]) / len(b["roi_flat"]) * 100 if b["roi_flat"] else None
        roi_value = sum(b["roi_value"]) / len(b["roi_value"]) * 100 if b["roi_value"] else None
        return {
            "n": n,
            "accuracy": round(sum(b["correct"]) / n * 100, 1) if n else None,
            "brier": round(sum(b["brier"]) / n, 4) if n else None,
            "log_loss": round(sum(b["log_loss"]) / n, 4) if n else None,
            "roi_flat_pct": round(roi_flat, 2) if roi_flat is not None else None,
            "value_bets": b["value_bets"],
            "roi_value_pct": round(roi_value, 2) if roi_value is not None else None,
        }

    summary = {p: agg(p) for p in pipelines}
    return {
        "mode": "historical",
        "league": league,
        "seasons": seasons or "all stored",
        "total_matches": len(matches),
        "seed": n_seed,
        "scored": n_score,
        "value_threshold": value_threshold,
        "duration_seconds": round(time.time() - started, 1),
        "summary": summary,
    }
