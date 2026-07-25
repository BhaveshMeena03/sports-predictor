"""Probability recalibration — one shrink factor per sport.

    calibrated_p = alpha * p + (1 - alpha) * (1/3)

    alpha < 1  -> model was overconfident, pull toward uniform
    alpha = 1  -> leave it alone
    alpha > 1  -> model was underconfident, sharpen

One parameter fitted on hundreds of matches: robust, and nothing richer
(isotonic, per-bin) is justified at this sample size.

Why per sport, not one global value
-----------------------------------
This started as a single alpha fitted on wc_match_log — 104 World Cup matches,
national teams, mostly neutral venues — and that value was then applied to every
prediction the system makes. Measured on the 2025-26 club seasons, the two
domains miscalibrate in OPPOSITE directions: the World Cup model was
overconfident (alpha 0.90) while the club models are underconfident (alpha
1.02-1.24). The global value was therefore pushing club probabilities the wrong
way.

The important safety property below: a sport with no fitted alpha gets 1.0 (no
adjustment). Borrowing another sport's correction is worse than doing nothing,
because it is confidently wrong rather than merely uncalibrated.
"""

import logging
from datetime import datetime, timezone

import aiosqlite

from app.core.database import DB_PATH

log = logging.getLogger(__name__)

# sport -> {"alpha": float, "n": int}. In-process cache over calibration_params.
_state: dict[str, dict] = {}

DEFAULT_SPORT = "international"

# Only these tables may be read by fit(); the name is interpolated into SQL, so
# an allowlist keeps that from ever becoming an injection point.
ALLOWED_LOG_TABLES = {"wc_match_log", "club_match_log", "cl_results"}


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS calibration_params (
                sport      TEXT PRIMARY KEY,
                alpha      REAL NOT NULL,
                n          INTEGER NOT NULL,
                brier_raw  REAL,
                brier_cal  REAL,
                fitted_at  TEXT NOT NULL
            )
        """)
        await db.commit()


def apply(probs: list[float], alpha: float | None = None,
          sport: str | None = None) -> list[float]:
    """Recalibrate a [pH, pD, pA] vector.

    Resolution order: explicit alpha > this sport's fitted alpha > 1.0. It does
    NOT fall back to another sport's value — see the module docstring.
    """
    if alpha is None:
        entry = _state.get(sport or DEFAULT_SPORT)
        alpha = entry["alpha"] if entry else 1.0
    out = [alpha * p + (1 - alpha) / 3 for p in probs]
    s = sum(out)
    return [p / s for p in out] if s else list(probs)


def current(sport: str | None = None) -> dict:
    """Fitted parameters for one sport, or every sport when called bare."""
    if sport is None:
        return {"by_sport": dict(_state), "default_sport": DEFAULT_SPORT}
    return dict(_state.get(sport, {"alpha": None, "n": 0}))


async def load() -> dict:
    """Warm the in-process cache from the calibration_params table."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT sport, alpha, n FROM calibration_params")).fetchall()
    _state.clear()
    for r in rows:
        _state[r["sport"]] = {"alpha": r["alpha"], "n": r["n"]}
    return dict(_state)


async def store(sport: str, alpha: float, n: int,
                brier_raw: float | None = None,
                brier_cal: float | None = None) -> None:
    """Persist a fitted alpha and refresh the cache."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO calibration_params
              (sport, alpha, n, brier_raw, brier_cal, fitted_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(sport) DO UPDATE SET
              alpha=excluded.alpha, n=excluded.n, brier_raw=excluded.brier_raw,
              brier_cal=excluded.brier_cal, fitted_at=excluded.fitted_at
        """, (sport, alpha, n, brier_raw, brier_cal,
              datetime.now(timezone.utc).isoformat(timespec="seconds")))
        await db.commit()
    _state[sport] = {"alpha": alpha, "n": n}


def _brier(data: list[tuple[list[float], int]], alpha: float) -> float:
    tot = 0.0
    for probs, idx in data:
        cp = apply(probs, alpha)
        tot += sum((cp[i] - (1.0 if idx == i else 0.0)) ** 2 for i in range(3)) / 3
    return tot / len(data)


def fit_alpha(data: list[tuple[list[float], int]]) -> tuple[float, float, float]:
    """Grid-search the alpha minimising Brier. Returns (alpha, raw, calibrated).

    Ties break toward 1.0 (no adjustment). This matters for degenerate fits: a
    model already predicting uniform is unchanged by shrinking toward uniform,
    so every alpha scores identically and a plain min() would return whichever
    end of the grid came first — claiming a large correction the data never
    supported.
    """
    grid = [round(0.40 + 0.02 * i, 2) for i in range(46)]   # 0.40 .. 1.30
    # Round the score so float noise doesn't defeat the tie-break.
    best = min(grid, key=lambda a: (round(_brier(data, a), 9), abs(a - 1.0)))
    return best, _brier(data, 1.0), _brier(data, best)


async def fit(sport: str = DEFAULT_SPORT, log_table: str = "wc_match_log") -> dict:
    """Fit alpha for one sport from its logged LIVE predictions.

    Club leagues have no live log until the season starts, so their alphas come
    from the walk-forward backtest instead — see calibration_report.fit_and_store().
    """
    if log_table not in ALLOWED_LOG_TABLES:
        raise ValueError(f"log_table must be one of {sorted(ALLOWED_LOG_TABLES)}")
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            f"SELECT home_goals, away_goals, p_home, p_draw, p_away "  # noqa: S608
            f"FROM {log_table} WHERE p_home IS NOT NULL")).fetchall()

    data = []
    for r in rows:
        if r["home_goals"] is None or r["away_goals"] is None:
            continue
        idx = (0 if r["home_goals"] > r["away_goals"]
               else 2 if r["home_goals"] < r["away_goals"] else 1)
        data.append(([r["p_home"], r["p_draw"], r["p_away"]], idx))

    if len(data) < 30:
        # Too little signal to fit anything; 1.0 leaves probabilities untouched.
        await store(sport, 1.0, len(data))
        return {"sport": sport, "alpha": 1.0, "n": len(data),
                "note": "too few matches, no adjustment applied"}

    best, raw, cal = fit_alpha(data)
    await store(sport, best, len(data), round(raw, 5), round(cal, 5))
    return {
        "sport": sport,
        "alpha": best,
        "n": len(data),
        "brier_raw": round(raw, 5),
        "brier_calibrated": round(cal, 5),
        "read": ("overconfident -> shrunk" if best < 0.98 else
                 "underconfident -> sharpened" if best > 1.02 else
                 "already well calibrated"),
    }
