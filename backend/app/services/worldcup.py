"""
World Cup 2026 readiness.

International football is where a systematic strength model adds the most value:
the markets are softer than top-5 club leagues, and casual bettors lean on gut
feel / reputation rather than current form.

We seed Elo ratings for national teams from approximate current international
strength (anchored on widely-known FIFA-ranking tiers as of early 2026). These
are PRIORS — they update automatically as tournament matches are played and
ingested via the normal Elo update path (sport='international').

NOTE: these seeds are deliberately conservative and tiered. They're a starting
point, not gospel — after a few matches the data takes over.
"""

import logging
import httpx
from app.core.config import settings
from app.services.elo import EloRatings

log = logging.getLogger(__name__)

SPORT = "international"

# API-Football league IDs for international competitions, with the free-tier
# seasons (2022-2024) that contain real matches. (league_id, season) pairs.
INTL_COMPETITIONS = [
    (1, 2022),    # World Cup 2022
    (4, 2024),    # Euro 2024
    (5, 2022),    # Nations League 2022
    (5, 2024),    # Nations League 2024
    (9, 2024),    # Copa America 2024
    (10, 2022),   # Friendlies 2022
    (10, 2023),   # Friendlies 2023
    (10, 2024),   # Friendlies 2024
]

# Approximate Elo seeds by strength tier (international football scale ~1300-2100).
# Tiers reflect general 2025-26 international strength. Update freely.
SEED_RATINGS: dict[str, float] = {
    # Tier 1 — elite contenders
    "Argentina": 2080, "France": 2060, "Spain": 2050, "England": 2010,
    "Brazil": 2000, "Portugal": 1990, "Netherlands": 1970, "Germany": 1955,
    # Tier 2 — strong
    "Belgium": 1930, "Italy": 1925, "Croatia": 1900, "Uruguay": 1895,
    "Colombia": 1880, "Morocco": 1875, "Switzerland": 1855, "Denmark": 1850,
    "USA": 1840, "Mexico": 1835, "Japan": 1830, "Senegal": 1820,
    # Tier 3 — solid
    "Ecuador": 1800, "Austria": 1795, "Korea Republic": 1785, "Australia": 1775,
    "Serbia": 1770, "Sweden": 1760, "Poland": 1755, "Ukraine": 1750,
    "Nigeria": 1745, "Canada": 1740, "Peru": 1730, "Wales": 1725,
    "Turkey": 1720, "Egypt": 1715, "Ivory Coast": 1710, "Iran": 1705,
    # Tier 4 — mid
    "Greece": 1690, "Scotland": 1685, "Norway": 1680, "Chile": 1675,
    "Tunisia": 1660, "Algeria": 1655, "Cameroon": 1650, "Ghana": 1645,
    "Paraguay": 1640, "Costa Rica": 1620, "Qatar": 1610, "Saudi Arabia": 1605,
    # Tier 5 — lower
    "Panama": 1560, "Jamaica": 1550, "South Africa": 1545, "Mali": 1540,
    "Honduras": 1520, "New Zealand": 1480, "Jordan": 1470, "Uzbekistan": 1465,
}


async def bootstrap_ratings(overwrite: bool = False) -> dict:
    """Seed national-team Elo ratings. By default only fills teams that don't
    already exist (so it won't clobber ratings learned from real matches)."""
    elo = EloRatings(SPORT)
    existing = await elo.get_many(list(SEED_RATINGS.keys()))
    seeded, skipped = 0, 0
    for team, rating in SEED_RATINGS.items():
        # get_many returns DEFAULT_RATING (1500) for unknown teams
        is_new = abs(existing.get(team, 1500) - 1500) < 1e-9
        if is_new or overwrite:
            await elo.set(team, rating)
            seeded += 1
        else:
            skipped += 1
    return {"sport": SPORT, "seeded": seeded, "skipped_existing": skipped,
            "total_teams": len(SEED_RATINGS)}


async def ingest_real_results(reset_to_seeds: bool = True) -> dict:
    """Pull real international matches from API-Football and update Elo chronologically.

    reset_to_seeds: if True, start from the hand-tiered seed priors then layer real
    2022-2024 results on top (so the ratings reflect prior strength + recent form).
    """
    from app.services.quota import api_football_quota

    if not settings.API_FOOTBALL_KEY:
        return {"error": "API_FOOTBALL_KEY required"}

    if reset_to_seeds:
        await bootstrap_ratings(overwrite=True)

    # Gather all fixtures across competitions
    all_fixtures = []
    pulled = {}
    headers = {"x-apisports-key": settings.API_FOOTBALL_KEY}
    async with httpx.AsyncClient(timeout=20.0) as client:
        for league_id, season in INTL_COMPETITIONS:
            if not api_football_quota.can_call():
                log.warning("quota exhausted during intl ingest")
                break
            try:
                resp = await client.get(
                    f"{settings.API_FOOTBALL_BASE}/fixtures",
                    headers=headers,
                    params={"league": league_id, "season": season},
                )
                api_football_quota.record()
                fixtures = resp.json().get("response", [])
                finished = [f for f in fixtures
                            if f.get("fixture", {}).get("status", {}).get("short") in ("FT", "AET", "PEN")]
                all_fixtures.extend(finished)
                pulled[f"{league_id}-{season}"] = len(finished)
            except Exception as e:
                log.warning("intl ingest fetch failed for %s-%s: %s", league_id, season, e)

    # Sort all matches chronologically (so Elo updates in true time order)
    all_fixtures.sort(key=lambda f: f["fixture"]["date"])

    elo = EloRatings(SPORT)
    ingested = 0
    for f in all_fixtures:
        try:
            home = f["teams"]["home"]["name"]
            away = f["teams"]["away"]["name"]
            hg = f["goals"]["home"]
            ag = f["goals"]["away"]
            if hg is None or ag is None:
                continue
            # International matches at neutral venues — but API gives a "home" team.
            # We keep the home/away as recorded; the small HFA bias washes out over
            # hundreds of matches and tournament hosts genuinely had home edge.
            await elo.update_after_match(home, away, hg, ag)
            ingested += 1
        except (KeyError, TypeError):
            continue

    return {
        "sport": SPORT,
        "competitions_pulled": pulled,
        "matches_ingested": ingested,
        "started_from": "seed priors" if reset_to_seeds else "existing ratings",
    }


async def predict(home: str, away: str, neutral: bool = True) -> dict:
    """Predict an international match. Most WC matches are at neutral venues, so
    by default we strip home advantage (handled via the EloRatings call below)."""
    elo = EloRatings(SPORT)
    # For neutral venues we still call predict_1x2 but note both teams equal HFA.
    # Simplest correct handling: predict normally, then if neutral, recompute
    # without the home bump by averaging both orientations.
    if not neutral:
        return await elo.predict_1x2(home, away)
    # Neutral: average home/away orientations to cancel the home-advantage term
    a = await elo.predict_1x2(home, away)
    b = await elo.predict_1x2(away, home)
    p_home = (a["p_home"] + b["p_away"]) / 2
    p_away = (a["p_away"] + b["p_home"]) / 2
    p_draw = (a["p_draw"] + b["p_draw"]) / 2
    total = p_home + p_draw + p_away
    return {
        "p_home": round(p_home / total, 4),
        "p_draw": round(p_draw / total, 4),
        "p_away": round(p_away / total, 4),
        "home_rating": a["home_rating"],
        "away_rating": a["away_rating"],
        "rating_diff": a["rating_diff"],
        "neutral_venue": True,
    }


# ─── Live tournament tracking (group stage onward) ──────────────────
#
# ESPN's fifa.world scoreboard is free (no key, no quota). Every day we:
#   1. Pull finished matches, dedupe via wc_match_log
#   2. Score our model's pre-match prediction (live calibration!)
#   3. Ingest the result into Elo at WC importance (k_scale=2.5)
# The scheduler calls daily_update() each morning; /worldcup/daily-update
# triggers it manually whenever the user checks in.

import aiosqlite
from datetime import datetime, timedelta, timezone
from app.core.database import DB_PATH

HOST_NATIONS = {"Mexico", "USA", "United States", "Canada"}
K_WORLD_CUP = 2.5
K_FRIENDLY = 0.33

# ESPN display names → our Elo DB names (API-Football style)
ESPN_NAME_MAP = {
    "DR Congo": "Congo DR",
    "Cape Verde": "Cape Verde Islands",
    "Korea Republic": "South Korea",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "Côte d'Ivoire": "Ivory Coast",
    "IR Iran": "Iran",
    "Czechia": "Czech Republic",
    "United States": "USA",
    "Curacao": "Curaçao",
}

def _norm(name: str) -> str:
    return ESPN_NAME_MAP.get(name, name)


# The 24 pre-tournament friendlies (re-applied at K_FRIENDLY after base rebuild)
PRE_WC_FRIENDLIES = [
    ("Saudi Arabia", 0, 0, "Senegal"), ("Argentina", 3, 0, "Iceland"),
    ("Croatia", 2, 1, "Slovenia"), ("Canada", 1, 1, "Republic of Ireland"),
    ("El Salvador", 0, 0, "Qatar"), ("Haiti", 2, 1, "Peru"),
    ("Ecuador", 3, 0, "Guatemala"), ("USA", 1, 2, "Germany"),
    ("Belgium", 5, 0, "Tunisia"), ("Portugal", 2, 1, "Chile"),
    ("Australia", 1, 1, "Switzerland"), ("Scotland", 4, 0, "Bolivia"),
    ("England", 1, 0, "New Zealand"), ("Panama", 1, 1, "Bosnia and Herzegovina"),
    ("Spain", 1, 1, "Iraq"), ("France", 1, 2, "Ivory Coast"),
    ("Mexico", 1, 0, "Australia"), ("Netherlands", 0, 1, "Algeria"),
    ("Japan", 1, 0, "Iceland"), ("Morocco", 1, 1, "Norway"),
    ("Argentina", 2, 0, "Honduras"), ("Brazil", 2, 1, "Egypt"),
    ("Venezuela", 1, 2, "Turkey"), ("Curaçao", 4, 0, "Aruba"),
]


async def _ensure_wc_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS wc_match_log (
                date TEXT NOT NULL,
                home TEXT NOT NULL,
                away TEXT NOT NULL,
                home_goals INTEGER,
                away_goals INTEGER,
                p_home REAL, p_draw REAL, p_away REAL,
                picked TEXT,
                correct INTEGER,
                brier REAL,
                PRIMARY KEY (date, home, away)
            );
        """)
        await db.commit()


async def rebuild_base_ratings() -> dict:
    """Full correct rebuild: seeds → 2022-24 competitive (K=1.0) →
    friendlies at K_FRIENDLY → already-logged WC results at K_WORLD_CUP."""
    out = {"base": await ingest_real_results(reset_to_seeds=True)}
    elo = EloRatings(SPORT)
    n = 0
    for h, hg, ag, a in PRE_WC_FRIENDLIES:
        try:
            await elo.update_after_match(h, a, hg, ag, k_scale=K_FRIENDLY)
            n += 1
        except Exception as e:
            log.warning("friendly replay failed %s vs %s: %s", h, a, e)
    out["friendlies_at_third_weight"] = n
    # Replay any WC results already logged (idempotent rebuild mid-tournament)
    await _ensure_wc_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM wc_match_log ORDER BY date")).fetchall()
    for r in rows:
        await elo.update_after_match(r["home"], r["away"],
                                     r["home_goals"], r["away_goals"],
                                     k_scale=K_WORLD_CUP)
    out["wc_results_replayed"] = len(rows)
    return out


async def fetch_espn_wc(date_str: str) -> list[dict]:
    """One day of WC fixtures/results from ESPN (free). date_str: YYYYMMDD."""
    url = f"{settings.ESPN_BASE}/soccer/fifa.world/scoreboard"
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
            "home": _norm(home.get("team", {}).get("displayName", "")),
            "away": _norm(away.get("team", {}).get("displayName", "")),
            "home_score": int(home["score"]) if home.get("score") not in (None, "") else None,
            "away_score": int(away["score"]) if away.get("score") not in (None, "") else None,
            "completed": bool(status.get("completed")),
            "status": status.get("name", ""),
        })
    return out


async def daily_update(days_back: int = 3) -> dict:
    """The daily ritual: ingest new results, score the model, preview today."""
    await _ensure_wc_tables()
    elo = EloRatings(SPORT)
    now = datetime.now(timezone.utc)

    new_results = []
    for delta in range(days_back, -1, -1):
        d = now - timedelta(days=delta)
        try:
            day = await fetch_espn_wc(d.strftime("%Y%m%d"))
        except Exception as e:
            log.warning("ESPN fetch failed for %s: %s", d.date(), e)
            continue
        for m in day:
            if not m["completed"] or m["home_score"] is None:
                continue
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(
                    "SELECT 1 FROM wc_match_log WHERE date=? AND home=? AND away=?",
                    (m["date"], m["home"], m["away"]))
                if await cur.fetchone():
                    continue  # already processed

            # Score the model BEFORE updating it (honest calibration)
            neutral = m["home"] not in HOST_NATIONS
            p = await predict(m["home"], m["away"], neutral=neutral)
            probs = [p["p_home"], p["p_draw"], p["p_away"]]
            actual_idx = 0 if m["home_score"] > m["away_score"] else (2 if m["home_score"] < m["away_score"] else 1)
            actual = [0.0, 0.0, 0.0]; actual[actual_idx] = 1.0
            picked_idx = max(range(3), key=lambda i: probs[i])
            brier = sum((probs[i] - actual[i]) ** 2 for i in range(3)) / 3
            labels = [m["home"], "Draw", m["away"]]

            upd = await elo.update_after_match(
                m["home"], m["away"], m["home_score"], m["away_score"],
                k_scale=K_WORLD_CUP)

            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """INSERT OR IGNORE INTO wc_match_log
                       (date, home, away, home_goals, away_goals,
                        p_home, p_draw, p_away, picked, correct, brier)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (m["date"], m["home"], m["away"], m["home_score"], m["away_score"],
                     probs[0], probs[1], probs[2], labels[picked_idx],
                     1 if picked_idx == actual_idx else 0, round(brier, 4)))
                await db.commit()

            new_results.append({
                "match": f'{m["home"]} {m["home_score"]}-{m["away_score"]} {m["away"]}',
                "date": m["date"],
                "model_said": f'{labels[picked_idx]} ({round(probs[picked_idx]*100)}%)',
                "model_correct": picked_idx == actual_idx,
                "brier": round(brier, 4),
                "elo_delta": upd["delta"],
            })

    # Today + tomorrow preview with fresh probabilities
    upcoming = []
    for delta in (0, 1):
        d = now + timedelta(days=delta)
        try:
            day = await fetch_espn_wc(d.strftime("%Y%m%d"))
        except Exception:
            continue
        for m in day:
            if m["completed"] or not m["home"] or not m["away"]:
                continue
            neutral = m["home"] not in HOST_NATIONS
            try:
                p = await predict(m["home"], m["away"], neutral=neutral)
                upcoming.append({
                    "date": m["date"], "match": f'{m["home"]} vs {m["away"]}',
                    "p_home": p["p_home"], "p_draw": p["p_draw"], "p_away": p["p_away"],
                })
            except Exception:
                continue

    # Running model scorecard
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            """SELECT COUNT(*) n, SUM(correct) c, AVG(brier) b
               FROM wc_match_log""")).fetchone()
    scorecard = {
        "matches_scored": row["n"] or 0,
        "model_accuracy_pct": round((row["c"] or 0) / row["n"] * 100, 1) if row["n"] else None,
        "avg_brier": round(row["b"], 4) if row["b"] is not None else None,
    }

    return {"new_results": new_results, "upcoming": upcoming,
            "model_scorecard": scorecard}
