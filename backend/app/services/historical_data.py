"""
Historical match data from football-data.co.uk.

Free CSVs with full-time results AND closing odds (Pinnacle + market average)
going back 20+ seasons. This gives us:
  - Far more training data than the free API-Football tier (1 season → many)
  - Real closing odds → backtested ROI (not just calibration)
  - A market-probability input for the ensemble

We download per (league, season), parse the columns we care about, and store
in a `historical_matches` SQLite table. Idempotent — re-running replaces rows.
"""

import csv
import io
import logging
import httpx
import aiosqlite
from app.core.database import DB_PATH

log = logging.getLogger(__name__)

BASE = "https://www.football-data.co.uk/mmz4281"

# football-data.co.uk league codes → our internal league keys
LEAGUE_CODES = {
    "premier_league": "E0",
    "la_liga": "SP1",
    "serie_a": "I1",
    "bundesliga": "D1",
    "ligue_1": "F1",
}

# Season codes: "2425" = 2024-25 season, etc.
DEFAULT_SEASONS = ["2425", "2324", "2223"]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS historical_matches (
                league TEXT NOT NULL,
                season TEXT NOT NULL,
                date TEXT,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                home_goals INTEGER,
                away_goals INTEGER,
                result TEXT,             -- H / D / A
                -- Pinnacle closing odds (sharpest line)
                ps_close_h REAL, ps_close_d REAL, ps_close_a REAL,
                -- Market average closing odds (fallback)
                avg_close_h REAL, avg_close_d REAL, avg_close_a REAL,
                PRIMARY KEY (league, season, home_team, away_team)
            );
        """)
        await db.commit()


def _f(row: dict, key: str) -> float | None:
    v = row.get(key, "")
    try:
        return float(v) if v not in ("", None) else None
    except ValueError:
        return None


def _i(row: dict, key: str) -> int | None:
    v = row.get(key, "")
    try:
        return int(float(v)) if v not in ("", None) else None
    except ValueError:
        return None


async def download_league_season(league_key: str, season: str) -> dict:
    """Download + store one league-season. Returns counts."""
    code = LEAGUE_CODES.get(league_key)
    if not code:
        raise ValueError(f"Unknown league_key: {league_key}")
    url = f"{BASE}/{season}/{code}.csv"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            raise RuntimeError(f"Download failed ({resp.status_code}) for {url}")
        text = resp.text

    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for r in reader:
        home = (r.get("HomeTeam") or "").strip()
        away = (r.get("AwayTeam") or "").strip()
        if not home or not away:
            continue
        # Prefer Pinnacle closing (PSC*), fall back to average closing (AvgC*),
        # then to non-closing Pinnacle (PS*) for older files.
        ps_h = _f(r, "PSCH") or _f(r, "PSH")
        ps_d = _f(r, "PSCD") or _f(r, "PSD")
        ps_a = _f(r, "PSCA") or _f(r, "PSA")
        rows.append((
            league_key, season, (r.get("Date") or "").strip(), home, away,
            _i(r, "FTHG"), _i(r, "FTAG"), (r.get("FTR") or "").strip(),
            ps_h, ps_d, ps_a,
            _f(r, "AvgCH"), _f(r, "AvgCD"), _f(r, "AvgCA"),
        ))

    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany("""
            INSERT INTO historical_matches
              (league, season, date, home_team, away_team, home_goals, away_goals, result,
               ps_close_h, ps_close_d, ps_close_a, avg_close_h, avg_close_d, avg_close_a)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(league, season, home_team, away_team) DO UPDATE SET
              home_goals=excluded.home_goals, away_goals=excluded.away_goals,
              result=excluded.result, ps_close_h=excluded.ps_close_h,
              ps_close_d=excluded.ps_close_d, ps_close_a=excluded.ps_close_a,
              avg_close_h=excluded.avg_close_h, avg_close_d=excluded.avg_close_d,
              avg_close_a=excluded.avg_close_a
        """, rows)
        await db.commit()

    return {"league": league_key, "season": season, "matches": len(rows)}


async def download_all(leagues: list[str] = None, seasons: list[str] = None) -> dict:
    leagues = leagues or list(LEAGUE_CODES.keys())
    seasons = seasons or DEFAULT_SEASONS
    results = []
    total = 0
    for lg in leagues:
        for sn in seasons:
            try:
                r = await download_league_season(lg, sn)
                results.append(r)
                total += r["matches"]
            except Exception as e:
                log.warning("download %s %s failed: %s", lg, sn, e)
                results.append({"league": lg, "season": sn, "error": str(e)})
    return {"total_matches": total, "downloads": results}


async def get_matches(
    league: str = None,
    seasons: list[str] = None,
    chronological: bool = True,
) -> list[dict]:
    """Fetch stored matches as dicts, optionally filtered + ordered by date."""
    await _ensure_table()
    q = "SELECT * FROM historical_matches WHERE 1=1"
    params = []
    if league:
        q += " AND league=?"
        params.append(league)
    if seasons:
        q += f" AND season IN ({','.join('?' * len(seasons))})"
        params.extend(seasons)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(q, params)).fetchall()
    matches = [dict(r) for r in rows]
    if chronological:
        # football-data dates are dd/mm/yyyy
        def keyf(m):
            d = m.get("date") or ""
            parts = d.split("/")
            return (parts[2], parts[1], parts[0]) if len(parts) == 3 else (d,)
        matches.sort(key=keyf)
    return matches
