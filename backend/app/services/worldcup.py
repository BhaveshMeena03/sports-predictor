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
