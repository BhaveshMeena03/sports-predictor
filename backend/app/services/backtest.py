"""
Backtesting harness.

Replays past matches through a prediction pipeline and scores the predictions
against actual results. This is the *measuring stick* — without it, every
"improvement" to the model is faith-based.

Metrics:
- Accuracy           : % of times the most-probable pick was right
- Brier score        : mean squared error of probabilities  (LOWER = better, perfect=0)
- Log-loss           : penalises confident-but-wrong harder (LOWER = better)
- Calibration buckets: actual win rate within each confidence bucket

Pipelines we can compare:
- 'elo'   : Elo only (deterministic, fast, free)
- 'llm'   : current LLM-based analyzer (slow, costs Anthropic credits)
- 'naive' : sanity baseline — always predict home win @ 1/3 each

Outputs are written to backtests/<run_id>.json so we can diff over time.
"""

import asyncio
import json
import logging
import math
import os
import time
from datetime import datetime

from app.core.config import settings
from app.core.database import DB_PATH
from app.services.elo import EloRatings, ingest_season_results
from app.services.football_service import football_service, LEAGUE_IDS
from app.services.ai_analyzer import ai_analyzer
from app.services.poisson import DixonColes
from app.services.ensemble import blend, get_weights

log = logging.getLogger(__name__)

BACKTEST_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "backtests"
)
os.makedirs(BACKTEST_DIR, exist_ok=True)


# ─── Metrics ─────────────────────────────────────────────────────

def actual_outcome_vector(home_goals: int, away_goals: int) -> list[float]:
    """One-hot: [P(home), P(draw), P(away)] for the actual result."""
    if home_goals > away_goals:
        return [1.0, 0.0, 0.0]
    if home_goals < away_goals:
        return [0.0, 0.0, 1.0]
    return [0.0, 1.0, 0.0]


def brier(predicted: list[float], actual: list[float]) -> float:
    """Multi-class Brier: mean sq error across the 3 outcomes."""
    return sum((p - a) ** 2 for p, a in zip(predicted, actual)) / len(predicted)


def log_loss(predicted: list[float], actual: list[float], eps: float = 1e-9) -> float:
    """Multi-class log loss. Punishes confident wrong predictions."""
    return -sum(a * math.log(max(p, eps)) for p, a in zip(predicted, actual))


def pick_from_probs(probs: list[float]) -> int:
    """Index of the most probable outcome (0=home, 1=draw, 2=away)."""
    return max(range(len(probs)), key=lambda i: probs[i])


# ─── Pipelines (each takes a fixture, returns 3-prob vector) ────

# DC models are fit once per league in run_backtest, then read by predict_poisson.
# Keyed by league_key (e.g. 'premier_league').
_DC_MODELS: dict[str, DixonColes] = {}


async def predict_naive(_fixture: dict, _sport: str) -> list[float]:
    return [1 / 3, 1 / 3, 1 / 3]


async def predict_poisson(fixture: dict, _sport: str) -> list[float]:
    """Dixon-Coles Poisson. Model must be pre-fitted in _DC_MODELS for this league."""
    home = fixture["teams"]["home"]["name"]
    away = fixture["teams"]["away"]["name"]
    # Find which league model to use — fixture has league name + id
    league_id = fixture.get("league", {}).get("id")
    league_key = next((k for k, v in LEAGUE_IDS.items() if v == league_id), None)
    model = _DC_MODELS.get(league_key) if league_key else None
    if not model:
        return [1 / 3, 1 / 3, 1 / 3]
    pred = model.predict_1x2(home, away)
    if not pred:
        return [1 / 3, 1 / 3, 1 / 3]
    return [pred["p_home"], pred["p_draw"], pred["p_away"]]


async def predict_elo(fixture: dict, sport: str) -> list[float]:
    home = fixture["teams"]["home"]["name"]
    away = fixture["teams"]["away"]["name"]
    elo = EloRatings(sport)
    p = await elo.predict_1x2(home, away)
    return [p["p_home"], p["p_draw"], p["p_away"]]


async def predict_llm(fixture: dict, sport: str) -> list[float]:
    """Bare LLM analyzer (no hydration).

    Why bare: production hydration pulls current-season form, which for a
    historical backtest match would include the match itself → data leakage.
    Honest backtesting requires point-in-time snapshots we don't have on the
    free tier. So this measures the LLM's pure prior knowledge — a lower bound."""
    home = fixture["teams"]["home"]["name"]
    away = fixture["teams"]["away"]["name"]
    match_data = {
        "sport": sport,
        "league": fixture.get("league", {}).get("name"),
        "home_team": home,
        "away_team": away,
        "date": fixture["fixture"]["date"][:10],
        "venue": fixture.get("fixture", {}).get("venue", {}).get("name"),
        "is_home": True,
    }
    analysis = await ai_analyzer.analyze_match(match_data)
    if not isinstance(analysis, dict) or "implied_probability" not in analysis:
        return [1 / 3, 1 / 3, 1 / 3]
    # Map the LLM's pick onto the 3-outcome vector
    p_pick = float(analysis["implied_probability"])
    rec = (analysis.get("recommendation") or "").lower()
    rest = (1 - p_pick) / 2
    if "home" in rec or home.lower() in rec:
        return [p_pick, rest, rest]
    if "away" in rec or away.lower() in rec:
        return [rest, rest, p_pick]
    if "draw" in rec:
        return [rest, p_pick, rest]
    if "skip" in rec:
        return [1 / 3, 1 / 3, 1 / 3]
    return [p_pick, rest, rest]


async def _ensemble_with(fixture: dict, sport: str, include_llm: bool) -> list[float]:
    weights, shrinkage = await get_weights(sport)
    if not include_llm:
        weights = {k: v for k, v in weights.items() if k != "llm"}
    preds = {}
    if weights.get("elo", 0) > 0:
        preds["elo"] = await predict_elo(fixture, sport)
    if weights.get("poisson", 0) > 0:
        preds["poisson"] = await predict_poisson(fixture, sport)
    if weights.get("llm", 0) > 0:
        preds["llm"] = await predict_llm(fixture, sport)
    return blend(preds, weights, shrinkage=shrinkage)


async def predict_ensemble(fixture: dict, sport: str) -> list[float]:
    """Full ensemble: Elo + Poisson + LLM. Slow + costs Anthropic credits."""
    return await _ensemble_with(fixture, sport, include_llm=True)


async def predict_ensemble_math(fixture: dict, sport: str) -> list[float]:
    """Math-only ensemble: Elo + Poisson, no LLM. Fast and free — use for big backtests."""
    return await _ensemble_with(fixture, sport, include_llm=False)


PIPELINES = {
    "naive": predict_naive,
    "elo": predict_elo,
    "poisson": predict_poisson,
    "llm": predict_llm,
    "ensemble_math": predict_ensemble_math,
    "ensemble": predict_ensemble,
}


# ─── Backtest runner ─────────────────────────────────────────────

async def fetch_finished_fixtures(league_key: str, season: int = None, limit: int = 100) -> list[dict]:
    """Pull all finished fixtures for a league/season from API-Football."""
    season = season or settings.API_FOOTBALL_SEASON
    league_id = LEAGUE_IDS.get(league_key)
    if not league_id:
        raise ValueError(f"Unknown league_key: {league_key}")
    import httpx
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{settings.API_FOOTBALL_BASE}/fixtures",
            headers={"x-apisports-key": settings.API_FOOTBALL_KEY},
            params={"league": league_id, "season": season},
        )
        data = resp.json()
    fixtures = data.get("response", [])
    finished = [f for f in fixtures if f.get("fixture", {}).get("status", {}).get("short") in ("FT", "AET", "PEN")]
    finished.sort(key=lambda f: f["fixture"]["date"])
    return finished[:limit] if limit else finished


async def _reset_elo_for_sport(sport: str) -> None:
    """Wipe Elo table for a sport so backtest starts from a clean slate
    (no leakage from prior runs)."""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS elo_ratings ("
                         "sport TEXT, team TEXT, rating REAL, matches INTEGER, updated_at TEXT, "
                         "PRIMARY KEY (sport, team))")
        await db.execute("DELETE FROM elo_ratings WHERE sport=?", (sport,))
        await db.commit()


async def run_backtest(
    leagues: list[str],
    pipelines: list[str],
    matches_per_league: int = 200,
    score_fraction: float = 0.3,
    score_cap: int | None = None,
) -> dict:
    """Replay matches through the chosen pipelines.

    matches_per_league : total fixtures pulled per league (default 200 — full-ish season)
    score_fraction     : last X% are SCORED; the rest seed Elo. Default 0.3 → 70% warmup
    score_cap          : optional cap on scored matches per league (for LLM cost control)
    """
    started = time.time()
    run_id = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    all_results = {p: [] for p in pipelines}
    fixtures_used = []

    # Clean slate for Elo (any prior data would leak into our score window)
    if "elo" in pipelines:
        await _reset_elo_for_sport("football")
    # Clear Dixon-Coles cache for this run
    if "poisson" in pipelines:
        _DC_MODELS.clear()

    for league in leagues:
        try:
            fixtures = await fetch_finished_fixtures(league, limit=matches_per_league)
        except Exception as e:
            log.warning("backtest: failed to fetch %s: %s", league, e)
            continue
        if not fixtures:
            continue

        n_score = max(1, int(len(fixtures) * score_fraction))
        n_seed = len(fixtures) - n_score
        log.info("backtest: %s — %d fixtures (%d seed + %d score)",
                 league, len(fixtures), n_seed, n_score)

        if "elo" in pipelines and n_seed > 0:
            await ingest_season_results("football", fixtures[:n_seed])
        # Fit Dixon-Coles on the seed window for this league
        if "poisson" in pipelines and n_seed > 0:
            seed_matches = []
            teams = set()
            for fx in fixtures[:n_seed]:
                h = fx["teams"]["home"]["name"]
                a = fx["teams"]["away"]["name"]
                hg = fx["goals"]["home"]
                ag = fx["goals"]["away"]
                if hg is None or ag is None:
                    continue
                seed_matches.append({"home": h, "away": a, "home_goals": hg, "away_goals": ag})
                teams.add(h); teams.add(a)
            if seed_matches:
                model = DixonColes(sorted(teams))
                try:
                    fit_info = model.fit(seed_matches, verbose=True)
                    _DC_MODELS[league] = model
                    log.info("DC fit for %s: %s", league, fit_info)
                except Exception as e:
                    log.warning("DC fit failed for %s: %s", league, e)

        score_fixtures = fixtures[n_seed:]
        if score_cap:
            score_fixtures = score_fixtures[:score_cap]

        for fx in score_fixtures:
            actual = actual_outcome_vector(fx["goals"]["home"], fx["goals"]["away"])
            row = {
                "league": league,
                "date": fx["fixture"]["date"][:10],
                "home": fx["teams"]["home"]["name"],
                "away": fx["teams"]["away"]["name"],
                "score": f'{fx["goals"]["home"]}-{fx["goals"]["away"]}',
                "actual": actual,
                "preds": {},
            }
            for p_name in pipelines:
                try:
                    probs = await PIPELINES[p_name](fx, "football")
                    row["preds"][p_name] = {
                        "probs": [round(x, 4) for x in probs],
                        "brier": round(brier(probs, actual), 4),
                        "log_loss": round(log_loss(probs, actual), 4),
                        "correct": pick_from_probs(probs) == pick_from_probs(actual),
                    }
                    all_results[p_name].append(row["preds"][p_name])
                except Exception as e:
                    log.warning("pipeline %s failed on %s vs %s: %s",
                                p_name, row["home"], row["away"], e)
            fixtures_used.append(row)

            # Update Elo with this result for next iteration (online learning)
            if "elo" in pipelines:
                elo = EloRatings("football")
                await elo.update_after_match(
                    row["home"], row["away"], fx["goals"]["home"], fx["goals"]["away"]
                )

    # ─── Aggregate ───
    summary = {}
    for p_name, rows in all_results.items():
        if not rows:
            continue
        n = len(rows)
        summary[p_name] = {
            "n": n,
            "accuracy": round(sum(1 for r in rows if r["correct"]) / n * 100, 1),
            "brier": round(sum(r["brier"] for r in rows) / n, 4),
            "log_loss": round(sum(r["log_loss"] for r in rows) / n, 4),
        }

    out = {
        "run_id": run_id,
        "leagues": leagues,
        "matches_per_league": matches_per_league,
        "score_fraction": score_fraction,
        "score_cap": score_cap,
        "pipelines": pipelines,
        "total_matches_scored": len(fixtures_used),
        "duration_seconds": round(time.time() - started, 1),
        "summary": summary,
        "fixtures": fixtures_used,
    }

    # Persist
    path = os.path.join(BACKTEST_DIR, f"{run_id}.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    out["saved_to"] = path
    return out
