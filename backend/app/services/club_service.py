"""
Premier League + La Liga prediction (v2 pipeline for clubs).

Data strategy (all free):
  - Ratings base: football-data.co.uk season CSVs (historical_matches table)
    replayed chronologically into per-league Elo namespaces
    ("club_epl", "club_laliga"). Includes the 2025-26 season -> current form.
  - In-season (from Aug 2026): ESPN scoreboards (eng.1 / esp.1, free, no key)
    ingested daily into club_match_log, scoring the model like the WC pipeline.
  - Predictions: the generalized draw-aware Poisson (intl_poisson.predict_v2)
    with league-specific Elo, baselines, and recent-form windows.

Names are normalized to full canonical names so football-data ("Man City"),
ESPN ("Manchester City") and user input all hit the same Elo rows.
"""

import logging
import unicodedata
import aiosqlite
import httpx
from datetime import datetime, timedelta, timezone
from app.core.config import settings
from app.core.database import DB_PATH
from app.services.elo import EloRatings
from app.services import calibration_layer
from app.services.intl_poisson import predict_v2

log = logging.getLogger(__name__)

LEAGUES = {
    "premier_league": {"sport": "club_epl",    "espn": "eng.1", "fd": "premier_league",
                       "baseline": 2.85, "label": "Premier League"},
    "la_liga":        {"sport": "club_laliga", "espn": "esp.1", "fd": "la_liga",
                       "baseline": 2.60, "label": "La Liga"},
    "serie_a":        {"sport": "club_seriea", "espn": "ita.1", "fd": "serie_a",
                       "baseline": 2.75, "label": "Serie A"},
    "bundesliga":     {"sport": "club_bund",   "espn": "ger.1", "fd": "bundesliga",
                       "baseline": 3.10, "label": "Bundesliga"},
    "ligue_1":        {"sport": "club_ligue1", "espn": "fra.1", "fd": "ligue_1",
                       "baseline": 2.65, "label": "Ligue 1"},
}

# football-data short names -> canonical (ESPN-style) names
FD_NAME_MAP = {
    # Premier League
    "Man City": "Manchester City", "Man United": "Manchester United",
    "Nott'm Forest": "Nottingham Forest", "Newcastle": "Newcastle United",
    "Tottenham": "Tottenham Hotspur", "West Ham": "West Ham United",
    "Wolves": "Wolverhampton Wanderers", "Leicester": "Leicester City",
    "Leeds": "Leeds United", "Ipswich": "Ipswich Town",
    "Sheffield United": "Sheffield United", "Brighton": "Brighton & Hove Albion",
    # La Liga
    "Ath Madrid": "Atletico Madrid", "Ath Bilbao": "Athletic Club",
    "Sociedad": "Real Sociedad", "Betis": "Real Betis",
    "Celta": "Celta Vigo", "Espanol": "Espanyol",
    "Vallecano": "Rayo Vallecano", "La Coruna": "Deportivo La Coruna",
    # Serie A / Bundesliga / Ligue 1 (for Champions League seeding)
    "Milan": "AC Milan", "Inter": "Inter Milan", "Roma": "AS Roma",
    "Dortmund": "Borussia Dortmund", "Leverkusen": "Bayer Leverkusen",
    "M'gladbach": "Borussia Monchengladbach", "Ein Frankfurt": "Eintracht Frankfurt",
    "FC Koln": "FC Cologne", "St Pauli": "St. Pauli",
    "Paris SG": "Paris Saint-Germain", "St Etienne": "Saint-Etienne",
}

# ESPN display names that differ from our canonical
# ESPN display name -> canonical (football-data) name, per league.
# Accent-only differences are handled by the fallback in norm_espn(); only
# genuinely different names need an entry here. Verified against every team
# ESPN returned across the full 2025-26 season — see validate_espn_names().
ESPN_NAME_MAP = {
    # Premier League
    "AFC Bournemouth": "Bournemouth",
    "Brighton & Hove Albion": "Brighton & Hove Albion",
    "Wolverhampton Wanderers": "Wolverhampton Wanderers",
    # La Liga
    "Athletic Club": "Athletic Club",
    "Atlético Madrid": "Atletico Madrid",
    "Deportivo Alavés": "Alaves",
    "Alavés": "Alaves",          # ESPN shortened this display name mid-season
    "Real Oviedo": "Oviedo",
    "Cádiz": "Cadiz", "Almería": "Almeria", "Leganés": "Leganes",
    # Serie A
    "Hellas Verona": "Verona",
    "Internazionale": "Inter Milan",
    # Bundesliga
    "1. FC Heidenheim 1846": "Heidenheim",
    "1. FC Union Berlin": "Union Berlin",
    "FC Augsburg": "Augsburg",
    "Hamburg SV": "Hamburg",
    "SC Freiburg": "Freiburg",
    "TSG Hoffenheim": "Hoffenheim",
    "VfB Stuttgart": "Stuttgart",
    "VfL Wolfsburg": "Wolfsburg",
    # Ligue 1
    "AJ Auxerre": "Auxerre",
    "AS Monaco": "Monaco",
    "Le Havre AC": "Le Havre",
    "Stade Rennais": "Rennes",
}

def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def norm_fd(name: str) -> str:   return FD_NAME_MAP.get(name, name)


def norm_espn(name: str) -> str:
    """ESPN display name -> the canonical (football-data) name Elo is keyed on.

    Explicit map first, then an accent-stripped fallback. The fallback matters:
    ESPN renamed "Deportivo Alavés" to "Alavés" mid-season and the lookup
    silently fell through to a default Elo rating instead of failing loudly.
    Accent-only drift now self-heals; genuine renames still need a map entry
    and are caught by validate_espn_names()."""
    if name in ESPN_NAME_MAP:
        return ESPN_NAME_MAP[name]
    return _strip_accents(name)


async def _ensure_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS club_match_log (
                league TEXT NOT NULL,
                date TEXT NOT NULL,
                home TEXT NOT NULL,
                away TEXT NOT NULL,
                home_goals INTEGER, away_goals INTEGER,
                p_home REAL, p_draw REAL, p_away REAL,
                picked TEXT, correct INTEGER, brier REAL,
                PRIMARY KEY (league, date, home, away)
            );
        """)
        # Raw (pre-calibration) vector alongside the served one. Refitting
        # alpha on served probabilities composes with the alpha already in
        # force and drifts on every refit — live refits must fit on raw.
        cur = await db.execute("PRAGMA table_info(club_match_log)")
        cols = {row[1] for row in await cur.fetchall()}
        for col in ("p_home_raw", "p_draw_raw", "p_away_raw"):
            if col not in cols:
                await db.execute(f"ALTER TABLE club_match_log ADD COLUMN {col} REAL")
        await db.commit()


async def rebuild_league_elo(league_key: str) -> dict:
    """Reset the league's Elo namespace and replay all stored football-data
    seasons chronologically (oldest first, home advantage handled by Elo)."""
    cfg = LEAGUES[league_key]
    sport = cfg["sport"]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM elo_ratings WHERE sport=?", (sport,))
        await db.commit()
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT date, home_team, away_team, home_goals, away_goals, season "
            "FROM historical_matches WHERE league=?", (cfg["fd"],))).fetchall()

    def date_key(m):
        p = (m["date"] or "").split("/")
        return (m["season"], p[2], p[1], p[0]) if len(p) == 3 else (m["season"],)
    matches = sorted(rows, key=date_key)

    elo = EloRatings(sport)
    n = 0
    for m in matches:
        if m["home_goals"] is None:
            continue
        await elo.update_after_match(norm_fd(m["home_team"]), norm_fd(m["away_team"]),
                                     m["home_goals"], m["away_goals"])
        n += 1
    top = await elo.all_ratings(limit=5)
    return {"league": league_key, "replayed": n,
            "top5": [f'{t["team"]} {t["rating"]:.0f}' for t in top]}


async def backtest_league_season(league_key: str, season: str = "2526",
                                 collect: bool = False) -> dict:
    """CL-style walk-forward validation for a domestic league: seed Elo from
    all seasons BEFORE `season`, then predict each of its matches blind
    (Elo + league baseline only — club_match_log is empty, so no goal-form
    leak from the future), score it, then update ratings.

    With `collect=True` the raw (model probs, market probs, outcome) triples are
    returned under "samples" so the calibration report can build reliability
    curves from exactly these predictions — one walk-forward implementation,
    not two that can silently disagree.

    Bonus: historical_matches carries closing odds, so we also score the MARKET
    on the same games — picking the closing favourite, and Brier on de-vigged
    closing probabilities. Source is Pinnacle where available, market-average
    otherwise (football-data.co.uk dropped Pinnacle columns on 2026-01-16);
    the returned benchmark reports the split."""
    cfg = LEAGUES[league_key]
    sport = cfg["sport"]
    await calibration_layer.load()
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM elo_ratings WHERE sport=?", (sport,))
        await db.commit()
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT date, home_team, away_team, home_goals, away_goals, season, "
            "ps_close_h, ps_close_d, ps_close_a, "
            "avg_close_h, avg_close_d, avg_close_a "
            "FROM historical_matches WHERE league=?", (cfg["fd"],))).fetchall()

    def date_key(m):
        p = (m["date"] or "").split("/")
        return (m["season"], p[2], p[1], p[0]) if len(p) == 3 else (m["season"],)
    matches = sorted(rows, key=date_key)

    elo = EloRatings(sport)
    n = correct = 0; brier_sum = 0.0; draws = draws_picked = 0
    mkt_n = mkt_correct = both = 0; mkt_brier_sum = model_brier_on_mkt = 0.0
    mkt_pinnacle = mkt_avg = 0    # which closing line each benchmarked match used
    log_rows = []
    samples: list[dict] = []
    for m in matches:
        if m["home_goals"] is None:
            continue
        h, a = norm_fd(m["home_team"]), norm_fd(m["away_team"])
        if m["season"] == season:
            p = await predict_v2(h, a, neutral=False, sport=sport,
                                 log_table="club_match_log",
                                 fallback_total=cfg["baseline"], recent_n=0)
            probs = [p["p_home"], p["p_draw"], p["p_away"]]
            idx = 0 if m["home_goals"] > m["away_goals"] else (2 if m["home_goals"] < m["away_goals"] else 1)
            pick = max(range(3), key=lambda i: probs[i])
            actual = [0.0, 0.0, 0.0]; actual[idx] = 1.0
            brier_sum += sum((probs[i] - actual[i]) ** 2 for i in range(3)) / 3
            n += 1; correct += (pick == idx)
            draws += (idx == 1); draws_picked += (pick == 1 and idx == 1)
            # football-data.co.uk stopped publishing Pinnacle closing odds on
            # 2026-01-16; market-average closing is still complete. Fall back so
            # the benchmark keeps covering the whole season instead of silently
            # collapsing onto its first half (which would bias the comparison).
            odds = ([m["ps_close_h"], m["ps_close_d"], m["ps_close_a"]]
                    if m["ps_close_h"] else
                    [m["avg_close_h"], m["avg_close_d"], m["avg_close_a"]])
            imp = None
            if all(odds):
                if m["ps_close_h"]:
                    mkt_pinnacle += 1
                else:
                    mkt_avg += 1
                imp = [1 / o for o in odds]
                s = sum(imp); imp = [x / s for x in imp]          # de-vig
                mkt_pick = max(range(3), key=lambda i: imp[i])
                mkt_n += 1; mkt_correct += (mkt_pick == idx)
                both += (pick == idx)                              # model on same subset
                mkt_brier_sum += sum((imp[i] - actual[i]) ** 2 for i in range(3)) / 3
                model_brier_on_mkt += sum((probs[i] - actual[i]) ** 2 for i in range(3)) / 3
            if collect:
                # Keep the pre-calibration vector too. Fitting alpha on the
                # calibrated output would compose with the alpha already applied,
                # so each refit would drift the value further (0.90 * 0.98 = 0.88
                # on the next pass, and so on). Calibration must always be fitted
                # against raw model output.
                samples.append({"date": m["date"], "home": h, "away": a,
                                "probs": probs, "probs_raw": p.get("raw", probs),
                                "market": imp, "outcome": idx})
            log_rows.append((m["date"], h, a,
                             f'{m["home_goals"]}-{m["away_goals"]}',
                             ["H", "D", "A"][pick], pick == idx))
        await elo.update_after_match(h, a, m["home_goals"], m["away_goals"])
    top = await elo.all_ratings(limit=5)
    out = {
        "league": cfg["label"], "season": season, "matches": n,
        "accuracy_pct": round(correct / n * 100, 1) if n else 0,
        "avg_brier": round(brier_sum / n, 4) if n else None,
        "draws_total": draws, "draws_correctly_picked": draws_picked,
        "final_top5": [f'{t["team"]} {t["rating"]:.0f}' for t in top],
        "last10": log_rows[-10:],
    }
    if mkt_n:
        out["market_benchmark"] = {
            "matches_with_closing_odds": mkt_n,
            # Mixed source since 2026-01-16 (see the fallback above), so report
            # the split rather than calling the whole thing "Pinnacle".
            "closing_odds_source": {"pinnacle": mkt_pinnacle, "market_avg": mkt_avg},
            "closing_favourite_accuracy_pct": round(mkt_correct / mkt_n * 100, 1),
            "model_accuracy_same_subset_pct": round(both / mkt_n * 100, 1),
            "closing_brier": round(mkt_brier_sum / mkt_n, 4),
            "model_brier_same_subset": round(model_brier_on_mkt / mkt_n, 4),
        }
    if collect:
        out["samples"] = samples
    return out


async def predict_club(league_key: str, home: str, away: str) -> dict:
    """Draw-aware v2 prediction for a club fixture (home advantage ON)."""
    cfg = LEAGUES[league_key]
    await calibration_layer.load()   # per-sport alphas; 1.0 if this sport is unfitted
    await _ensure_tables()
    # club_match_log is empty until the 2026-27 season starts; predict_v2 then
    # falls back to the league baseline, and the Elo (replayed through 2025-26)
    # carries current team strength.
    out = await predict_v2(home, away, neutral=False,
                           sport=cfg["sport"], log_table="club_match_log",
                           fallback_total=cfg["baseline"], recent_n=10)
    out["league"] = cfg["label"]
    return out


async def fetch_espn_league(league_key: str, date_str: str) -> list[dict]:
    """One day of fixtures/results from ESPN for a club league."""
    cfg = LEAGUES[league_key]
    url = f"{settings.ESPN_BASE}/soccer/{cfg['espn']}/scoreboard"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, params={"dates": date_str})
        data = resp.json()
    out = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        comps = comp.get("competitors", [])
        home = next((c for c in comps if c.get("homeAway") == "home"), {})
        away = next((c for c in comps if c.get("homeAway") == "away"), {})
        status = ev.get("status", {}).get("type", {})
        out.append({
            "date": (ev.get("date") or "")[:10],
            "home": norm_espn(home.get("team", {}).get("displayName", "")),
            "away": norm_espn(away.get("team", {}).get("displayName", "")),
            "home_score": int(home["score"]) if home.get("score") not in (None, "") else None,
            "away_score": int(away["score"]) if away.get("score") not in (None, "") else None,
            "completed": bool(status.get("completed")),
        })
    return out


async def known_elo_teams(sport: str) -> set[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute(
            "SELECT team FROM elo_ratings WHERE sport=?", (sport,))).fetchall()
    return {r[0] for r in rows}


async def validate_espn_names(days_back: int = 14) -> dict:
    """Health check: does every ESPN team name we've seen recently resolve to a
    team Elo actually has a rating for?

    ESPN display names drift (they renamed "Deportivo Alavés" to "Alavés"
    mid-season). Un-resolved names don't raise — predict_v2 just falls back to a
    default rating — so the model silently gets worse with no error anywhere.
    Run this before/at season start and after any ESPN-side change."""
    now = datetime.now(timezone.utc)
    report = {}
    for league_key, cfg in LEAGUES.items():
        known = await known_elo_teams(cfg["sport"])
        seen: set[str] = set()
        for delta in range(days_back, -1, -1):
            d = (now - timedelta(days=delta)).strftime("%Y%m%d")
            try:
                for m in await fetch_espn_league(league_key, d):
                    seen.update(x for x in (m["home"], m["away"]) if x)
            except Exception as e:
                log.warning("name-validation fetch %s %s failed: %s", league_key, d, e)
        unmatched = sorted(seen - known)
        report[league_key] = {
            "espn_teams_seen": len(seen),
            "resolved": len(seen & known),
            "unmatched": unmatched,
        }
        if unmatched:
            log.error("ESPN name drift in %s — unmapped teams %s; add them to "
                      "ESPN_NAME_MAP or predictions will use default ratings",
                      league_key, unmatched)
    return report


async def daily_update_clubs(days_back: int = 3) -> dict:
    """In-season ritual: ingest finished PL/La Liga matches, score the model,
    update league Elo. Idempotent via club_match_log primary key."""
    await _ensure_tables()
    now = datetime.now(timezone.utc)
    summary = {}
    for league_key, cfg in LEAGUES.items():
        elo = EloRatings(cfg["sport"])
        known = await known_elo_teams(cfg["sport"])
        new = []
        for delta in range(days_back, -1, -1):
            d = (now - timedelta(days=delta)).strftime("%Y%m%d")
            try:
                day = await fetch_espn_league(league_key, d)
            except Exception as e:
                log.warning("ESPN %s fetch failed: %s", league_key, e)
                continue
            for m in day:
                if not m["completed"] or m["home_score"] is None:
                    continue
                async with aiosqlite.connect(DB_PATH) as db:
                    cur = await db.execute(
                        "SELECT 1 FROM club_match_log WHERE league=? AND date=? AND home=? AND away=?",
                        (league_key, m["date"], m["home"], m["away"]))
                    if await cur.fetchone():
                        continue
                unknown = [t for t in (m["home"], m["away"]) if t not in known]
                if unknown:
                    # Not fatal (predict_v2 falls back to a default rating), but
                    # it silently degrades the prediction — so say so loudly.
                    log.error("%s %s: unmapped team(s) %s — prediction will use "
                              "default Elo; add to ESPN_NAME_MAP",
                              league_key, m["date"], unknown)
                p = await predict_club(league_key, m["home"], m["away"])
                probs = [p["p_home"], p["p_draw"], p["p_away"]]
                idx = 0 if m["home_score"] > m["away_score"] else (2 if m["home_score"] < m["away_score"] else 1)
                actual = [0.0, 0.0, 0.0]; actual[idx] = 1.0
                pick_i = max(range(3), key=lambda i: probs[i])
                brier = sum((probs[i] - actual[i]) ** 2 for i in range(3)) / 3
                labels = [m["home"], "Draw", m["away"]]
                await elo.update_after_match(m["home"], m["away"],
                                             m["home_score"], m["away_score"])
                raw = p.get("raw", probs)
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        """INSERT OR IGNORE INTO club_match_log
                           (league,date,home,away,home_goals,away_goals,
                            p_home,p_draw,p_away,picked,correct,brier,
                            p_home_raw,p_draw_raw,p_away_raw)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (league_key, m["date"], m["home"], m["away"],
                         m["home_score"], m["away_score"], *probs,
                         labels[pick_i], 1 if pick_i == idx else 0, round(brier, 4),
                         *raw))
                    await db.commit()
                new.append(f'{m["home"]} {m["home_score"]}-{m["away_score"]} {m["away"]}')
        summary[league_key] = new
    return summary
