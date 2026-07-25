"""
Champions League: cross-league Elo + walk-forward backtest vs last season.

The core problem CL adds: teams come from different domestic leagues whose
Elo scales float independently (a 1700 in the PL != a 1700 in Ligue 1).
Solution:
  1. Seed a combined "club_europe" namespace from each team's DOMESTIC Elo
     plus a league-strength offset (crude prior).
  2. Harvest the ACTUAL 2025-26 CL season from ESPN (free) into cl_results.
  3. Walk forward through those matches chronologically: predict each with
     only prior information, score it (accuracy/Brier), then update ratings.
     Cross-league results self-correct the offsets as the season replays.
This is exactly the validation the user asked for: "run the sim and compare
with the match results from the previous season."
"""

import logging
import aiosqlite
import httpx
from datetime import date, timedelta
from app.core.config import settings
from app.core.database import DB_PATH
from app.services.elo import EloRatings
from app.services import calibration_layer
from app.services.intl_poisson import predict_v2
from app.services.club_service import LEAGUES

log = logging.getLogger(__name__)

CL_SPORT = "club_europe"
K_CL = 1.2

# League-strength offsets added to domestic Elo when seeding Europe-wide
# ratings (rough priors; the walk-forward self-corrects with real results).
OFFSETS = {"club_epl": 60, "club_laliga": 45, "club_seriea": 30,
           "club_bund": 30, "club_ligue1": 20}
NON_BIG5_DEFAULT = 1560   # Porto, Benfica, Ajax, Celtic, PSV, ...

# ESPN CL display names -> our canonical (domestic) names
CL_NAME_MAP = {
    "Internazionale": "Inter Milan", "Inter": "Inter Milan",
    "Atlético Madrid": "Atletico Madrid", "Athletic Club": "Athletic Club",
    "Paris Saint-Germain": "Paris Saint-Germain", "PSG": "Paris Saint-Germain",
    "Bayern München": "Bayern Munich", "1. FC Köln": "FC Cologne",
    "Sporting CP": "Sporting Lisbon",
}
def norm_cl(n: str) -> str: return CL_NAME_MAP.get(n, n)


async def _ensure_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS cl_results (
                season TEXT NOT NULL,
                date TEXT NOT NULL,
                home TEXT NOT NULL,
                away TEXT NOT NULL,
                home_goals INTEGER, away_goals INTEGER,
                PRIMARY KEY (season, date, home, away)
            );
        """)
        await db.commit()


async def harvest_season(start: str = "2025-09-01", end: str = "2026-06-05",
                         season: str = "2025-26") -> dict:
    """Fetch the full CL season from ESPN scoreboards. CL plays Tue/Wed/Thu
    (+ a few weekend finals dates) — we only hit those days to keep it light."""
    await _ensure_tables()
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    fetched = stored = 0
    async with httpx.AsyncClient(timeout=15.0) as client:
        d = d0
        while d <= d1:
            if d.weekday() in (1, 2, 3, 5):   # Tue, Wed, Thu, Sat(final)
                try:
                    resp = await client.get(
                        f"{settings.ESPN_BASE}/soccer/uefa.champions/scoreboard",
                        params={"dates": d.strftime("%Y%m%d")})
                    events = resp.json().get("events", [])
                    fetched += 1
                except Exception as e:
                    log.warning("CL fetch %s failed: %s", d, e)
                    d += timedelta(days=1); continue
                for ev in events:
                    comp = (ev.get("competitions") or [{}])[0]
                    comps = comp.get("competitors", [])
                    home = next((c for c in comps if c.get("homeAway") == "home"), {})
                    away = next((c for c in comps if c.get("homeAway") == "away"), {})
                    status = ev.get("status", {}).get("type", {})
                    if not status.get("completed"):
                        continue
                    hs = home.get("score"); as_ = away.get("score")
                    if hs in (None, "") or as_ in (None, ""):
                        continue
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute(
                            "INSERT OR IGNORE INTO cl_results VALUES (?,?,?,?,?,?)",
                            (season, (ev.get("date") or "")[:10],
                             norm_cl(home.get("team", {}).get("displayName", "")),
                             norm_cl(away.get("team", {}).get("displayName", "")),
                             int(hs), int(as_)))
                        await db.commit()
                    stored += 1
            d += timedelta(days=1)
    return {"season": season, "days_fetched": fetched, "matches_stored": stored}


async def seed_ratings() -> dict:
    """Wipe club_europe and seed every big-5 team from its domestic Elo +
    league offset. Non-big-5 CL teams default when first seen."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM elo_ratings WHERE sport=?", (CL_SPORT,))
        await db.commit()
    euro = EloRatings(CL_SPORT)
    seeded = 0
    for cfg in LEAGUES.values():
        dom = EloRatings(cfg["sport"])
        for row in await dom.all_ratings(limit=40):
            await euro.set(row["team"], row["rating"] + OFFSETS[cfg["sport"]])
            seeded += 1
    return {"seeded_from_big5": seeded, "non_big5_default": NON_BIG5_DEFAULT}


async def _default_unknowns(*teams):
    euro = EloRatings(CL_SPORT)
    r = await euro.get_many(list(teams))
    for t, v in r.items():
        if abs(v - 1500) < 1e-9:          # unseen -> European-competitor prior
            await euro.set(t, NON_BIG5_DEFAULT)


async def backtest_last_season(season: str = "2025-26") -> dict:
    """THE validation: walk forward through the harvested CL season,
    predict each match with only prior info, score, update. No hindsight."""
    await _ensure_tables()
    await calibration_layer.load()
    await seed_ratings()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM cl_results WHERE season=? ORDER BY date", (season,))).fetchall()
    if not rows:
        return {"error": "no harvested results — run /cl/harvest first"}

    euro = EloRatings(CL_SPORT)
    n = correct = 0; brier_sum = 0.0; draws = draws_picked = 0
    log_rows = []
    for m in rows:
        await _default_unknowns(m["home"], m["away"])
        p = await predict_v2(m["home"], m["away"], neutral=False,
                             sport=CL_SPORT, log_table="cl_results",
                             fallback_total=2.9, recent_n=8)
        probs = [p["p_home"], p["p_draw"], p["p_away"]]
        idx = 0 if m["home_goals"] > m["away_goals"] else (2 if m["home_goals"] < m["away_goals"] else 1)
        pick = max(range(3), key=lambda i: probs[i])
        actual = [0.0, 0.0, 0.0]; actual[idx] = 1.0
        brier_sum += sum((probs[i] - actual[i]) ** 2 for i in range(3)) / 3
        n += 1; correct += (pick == idx)
        draws += (idx == 1); draws_picked += (pick == 1 and idx == 1)
        await euro.update_after_match(m["home"], m["away"],
                                      m["home_goals"], m["away_goals"], k_scale=K_CL)
        log_rows.append((m["date"], m["home"], m["away"],
                         f'{m["home_goals"]}-{m["away_goals"]}',
                         ["H", "D", "A"][pick], pick == idx))
    top = await euro.all_ratings(limit=8)
    return {
        "season": season, "matches": n,
        "accuracy_pct": round(correct / n * 100, 1),
        "avg_brier": round(brier_sum / n, 4),
        "draws_total": draws, "draws_correctly_picked": draws_picked,
        "final_top8": [f'{t["team"]} {t["rating"]:.0f}' for t in top],
        "last10": log_rows[-10:],
    }


async def predict_cl(home: str, away: str, neutral: bool = False) -> dict:
    """CL fixture prediction on the post-backtest European ratings."""
    await calibration_layer.load()
    await _default_unknowns(home, away)
    out = await predict_v2(home, away, neutral=neutral, sport=CL_SPORT,
                           log_table="cl_results", fallback_total=2.9, recent_n=8)
    out["competition"] = "Champions League"
    return out
