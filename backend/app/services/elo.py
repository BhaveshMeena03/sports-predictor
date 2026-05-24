"""
Self-updating Elo rating system for sports prediction.

Why Elo? It's the simplest credible probabilistic strength model. Standard chess
formula adapted for football (with home advantage + margin-of-victory K-factor).
After ~1 season of results it tracks team strength tightly.

How we use it:
  - Bootstrap: feed it a season of results, it converges
  - Predict: convert rating diff → P(home_win), P(draw) (via empirical draw rate)
  - Ensemble: this prob becomes a feature alongside LLM + Dixon-Coles

Persistence: ratings live in SQLite (`elo_ratings` table, auto-created).
"""

import math
import logging
import aiosqlite
from app.core.database import DB_PATH

log = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────────

DEFAULT_RATING = 1500
HOME_ADVANTAGE = 65    # ~5% win-prob bump, calibrated to top-flight football
K_FACTOR_BASE = 20     # standard chess value; we scale by goal margin
DRAW_PROB_BASE = 0.27  # empirical EPL draw rate; will be sport-specific later


class EloRatings:
    """Per-sport Elo store. Each (sport, team_name) keeps a rating."""

    def __init__(self, sport: str):
        self.sport = sport

    # ─── DB helpers ──────────────────────────────────────────────

    async def _ensure_table(self, db: aiosqlite.Connection) -> None:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS elo_ratings (
                sport TEXT NOT NULL,
                team TEXT NOT NULL,
                rating REAL NOT NULL,
                matches INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (sport, team)
            );
        """)
        await db.commit()

    async def get(self, team: str) -> float:
        async with aiosqlite.connect(DB_PATH) as db:
            await self._ensure_table(db)
            cur = await db.execute(
                "SELECT rating FROM elo_ratings WHERE sport=? AND team=?",
                (self.sport, team.strip()),
            )
            row = await cur.fetchone()
        return row[0] if row else DEFAULT_RATING

    async def get_many(self, teams: list[str]) -> dict[str, float]:
        async with aiosqlite.connect(DB_PATH) as db:
            await self._ensure_table(db)
            placeholders = ",".join("?" * len(teams))
            cur = await db.execute(
                f"SELECT team, rating FROM elo_ratings WHERE sport=? AND team IN ({placeholders})",
                (self.sport, *[t.strip() for t in teams]),
            )
            rows = await cur.fetchall()
        existing = {r[0]: r[1] for r in rows}
        return {t: existing.get(t.strip(), DEFAULT_RATING) for t in teams}

    async def set(self, team: str, rating: float) -> None:
        async with aiosqlite.connect(DB_PATH) as db:
            await self._ensure_table(db)
            await db.execute(
                """INSERT INTO elo_ratings (sport, team, rating, matches, updated_at)
                   VALUES (?, ?, ?, 0, datetime('now'))
                   ON CONFLICT(sport, team) DO UPDATE
                   SET rating=excluded.rating, updated_at=datetime('now')""",
                (self.sport, team.strip(), rating),
            )
            await db.commit()

    # ─── Core math ───────────────────────────────────────────────

    @staticmethod
    def expected(rating_a: float, rating_b: float) -> float:
        """Standard Elo expected-score formula. Returns 0..1."""
        return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))

    @staticmethod
    def k_for_margin(goal_margin: int) -> float:
        """Margin-of-victory adjustment (Fivethirtyeight football style).
        Bigger wins update the rating more, with diminishing returns."""
        if goal_margin <= 1:
            mult = 1.0
        elif goal_margin == 2:
            mult = 1.5
        elif goal_margin == 3:
            mult = 1.75
        else:
            mult = 1.75 + (goal_margin - 3) / 8.0
        return K_FACTOR_BASE * mult

    # ─── Predict ────────────────────────────────────────────────

    async def predict_1x2(self, home_team: str, away_team: str) -> dict:
        """Return probability dict for the three outcomes.

        Approach: pure two-outcome Elo (home/away), then carve out a fixed draw
        slice. Simple, well-calibrated for top-flight football."""
        ratings = await self.get_many([home_team, away_team])
        home_r = ratings[home_team] + HOME_ADVANTAGE
        away_r = ratings[away_team]
        p_home_2way = self.expected(home_r, away_r)
        # Allocate draw probability proportionally based on how close the match is
        # Closer match → higher draw share. We anchor on DRAW_PROB_BASE at parity
        # and fade it as the match becomes more lopsided.
        closeness = 1.0 - abs(p_home_2way - 0.5) * 2  # 1 at 0.5, 0 at 0 or 1
        p_draw = DRAW_PROB_BASE * closeness
        # Remaining prob split by 2-way Elo
        remaining = 1.0 - p_draw
        p_home = remaining * p_home_2way
        p_away = remaining * (1 - p_home_2way)
        return {
            "p_home": round(p_home, 4),
            "p_draw": round(p_draw, 4),
            "p_away": round(p_away, 4),
            "home_rating": round(ratings[home_team], 1),
            "away_rating": round(ratings[away_team], 1),
            "rating_diff": round(ratings[home_team] - ratings[away_team], 1),
        }

    # ─── Update after a result ──────────────────────────────────

    async def update_after_match(
        self,
        home_team: str,
        away_team: str,
        home_goals: int,
        away_goals: int,
    ) -> dict:
        """Update both team ratings after a finished match."""
        ratings = await self.get_many([home_team, away_team])
        home_r = ratings[home_team]
        away_r = ratings[away_team]
        # Elo expected score (factor in home advantage but DON'T persist it)
        expected_home = self.expected(home_r + HOME_ADVANTAGE, away_r)
        if home_goals > away_goals:
            actual_home = 1.0
        elif home_goals < away_goals:
            actual_home = 0.0
        else:
            actual_home = 0.5
        k = self.k_for_margin(abs(home_goals - away_goals))
        delta = k * (actual_home - expected_home)
        new_home = home_r + delta
        new_away = away_r - delta
        await self.set(home_team, new_home)
        await self.set(away_team, new_away)
        return {
            "home_rating_before": round(home_r, 1),
            "home_rating_after": round(new_home, 1),
            "away_rating_before": round(away_r, 1),
            "away_rating_after": round(new_away, 1),
            "delta": round(delta, 2),
            "k_used": round(k, 1),
        }

    async def all_ratings(self, limit: int = 50) -> list[dict]:
        async with aiosqlite.connect(DB_PATH) as db:
            await self._ensure_table(db)
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT team, rating, matches, updated_at FROM elo_ratings
                   WHERE sport=? ORDER BY rating DESC LIMIT ?""",
                (self.sport, limit),
            )
            rows = await cur.fetchall()
        return [
            {"team": r["team"], "rating": round(r["rating"], 1),
             "matches": r["matches"], "updated_at": r["updated_at"]}
            for r in rows
        ]


# ─── Bulk-ingest from API-Football fixtures ──────────────────────

async def ingest_season_results(sport: str, fixtures: list[dict]) -> dict:
    """Feed a season of API-Football fixtures into the Elo system.

    Returns counts so the caller can report. Fixtures must have
    teams.{home,away}.name + goals.{home,away} and a 'finished' status."""
    elo = EloRatings(sport)
    ingested = 0
    skipped = 0
    for m in fixtures:
        try:
            short = m.get("fixture", {}).get("status", {}).get("short")
            if short not in ("FT", "AET", "PEN"):
                skipped += 1
                continue
            home = m["teams"]["home"]["name"]
            away = m["teams"]["away"]["name"]
            hg = m["goals"]["home"]
            ag = m["goals"]["away"]
            if hg is None or ag is None:
                skipped += 1
                continue
            await elo.update_after_match(home, away, hg, ag)
            ingested += 1
        except (KeyError, TypeError) as e:
            log.warning("Elo ingest skipped a fixture: %s", e)
            skipped += 1
    return {"ingested": ingested, "skipped": skipped, "sport": sport}
