"""
Ensemble blender — combines multiple 1X2 probability vectors into a single,
better-calibrated prediction.

Why ensemble: each model captures different signal.
  - Elo:       team strength from results, well-calibrated
  - Dixon-Coles: scoring rates, captures goal-differential not just W/D/L
  - LLM:       news / narrative / soft factors, but tends to be over-confident

Blending strategy:
  1. Weighted average of probability vectors (weights tunable per-sport)
  2. Optional 'shrinkage' toward uniform prior (anti-overconfidence)
  3. Renormalise

Default weights come from the latest backtest run (lower Brier → higher weight).
Stored in the `ensemble_weights` table per-sport so we can re-tune over time.
"""

import json
import logging
import aiosqlite
from app.core.database import DB_PATH

log = logging.getLogger(__name__)


# ─── Persistence ─────────────────────────────────────────────────

async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS ensemble_weights (
                sport TEXT PRIMARY KEY,
                weights_json TEXT NOT NULL,
                shrinkage REAL DEFAULT 0.0,
                updated_at TEXT DEFAULT (datetime('now'))
            );
        """)
        await db.commit()


# Backtest-tuned defaults (240-match cross-league run, 2026-05-20):
#   - math-only ensemble (Elo+Poisson) beats either alone on Brier + log-loss
#   - LLM at 0.30 weight HURT the ensemble (sample n=20) — over-confident, adds noise
#   - 0.15 LLM weight keeps the narrative/news signal without dragging Brier down
DEFAULT_WEIGHTS = {
    "football": {"elo": 0.425, "poisson": 0.425, "llm": 0.15},
    "nba":      {"elo": 0.70, "llm": 0.30},   # no Poisson for NBA yet
    "nhl":      {"elo": 0.70, "llm": 0.30},
    "cricket":  {"llm": 1.00},                # LLM-only for now
    "ipl":      {"llm": 1.00},
    # International: data-driven Elo is strong; LLM adds squad/injury/news context.
    # No Poisson (national teams play too few games to fit goal rates reliably).
    "international": {"elo": 0.60, "llm": 0.40},
}
DEFAULT_SHRINKAGE = {"football": 0.10, "nba": 0.10, "nhl": 0.10,
                     "cricket": 0.0, "ipl": 0.0, "international": 0.08}


async def get_weights(sport: str) -> tuple[dict[str, float], float]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT weights_json, shrinkage FROM ensemble_weights WHERE sport=?", (sport,)
        )
        row = await cur.fetchone()
    if row:
        return json.loads(row[0]), float(row[1] or 0)
    return DEFAULT_WEIGHTS.get(sport, {"llm": 1.0}), DEFAULT_SHRINKAGE.get(sport, 0.0)


async def set_weights(sport: str, weights: dict[str, float], shrinkage: float = 0.0) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO ensemble_weights (sport, weights_json, shrinkage)
               VALUES (?, ?, ?)
               ON CONFLICT(sport) DO UPDATE
               SET weights_json=excluded.weights_json, shrinkage=excluded.shrinkage,
                   updated_at=datetime('now')""",
            (sport, json.dumps(weights), shrinkage),
        )
        await db.commit()


# ─── Blending ────────────────────────────────────────────────────

def blend(
    predictions: dict[str, list[float]],
    weights: dict[str, float],
    shrinkage: float = 0.0,
) -> list[float]:
    """Weighted average of P=[p_home, p_draw, p_away] vectors.

    predictions: {model_name: [p_h, p_d, p_a]}
    weights    : {model_name: w}   (only models present in BOTH dicts are used)
    shrinkage  : 0..1, how much to pull final probs toward uniform [1/3,1/3,1/3]
                 0.1 means "trust uniform 10%, trust blended 90%"

    Returns a normalised 3-element probability vector.
    """
    usable = {k: weights[k] for k in predictions if k in weights and weights[k] > 0}
    if not usable:
        return [1 / 3, 1 / 3, 1 / 3]

    total_w = sum(usable.values())
    blended = [0.0, 0.0, 0.0]
    for model, w in usable.items():
        probs = predictions[model]
        for i in range(3):
            blended[i] += probs[i] * (w / total_w)

    # Shrinkage toward uniform — anti-overconfidence
    if shrinkage > 0:
        uniform = 1 / 3
        for i in range(3):
            blended[i] = (1 - shrinkage) * blended[i] + shrinkage * uniform

    # Renormalise (rounding/shrinkage can drift slightly)
    total = sum(blended)
    if total > 0:
        blended = [p / total for p in blended]
    return [round(p, 4) for p in blended]


# ─── Convenience: pick from blended ─────────────────────────────

def pick_from_blend(blended: list[float], home: str, away: str) -> dict:
    """Translate a blended probability vector into a single recommendation."""
    labels = [f"{home} Win", "Draw", f"{away} Win"]
    best = max(range(3), key=lambda i: blended[i])
    confidence = round(blended[best] * 100, 1)
    return {
        "recommendation": labels[best],
        "confidence": confidence,
        "implied_probability": round(blended[best], 4),
        "p_home": blended[0],
        "p_draw": blended[1],
        "p_away": blended[2],
    }
