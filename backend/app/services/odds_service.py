import httpx
import json
import time
from app.core.config import settings
from app.services.quota import odds_api_quota

class OddsService:
    """Fetches live odds from The Odds API (free tier: 500 requests/month).
    Includes caching to save API requests."""

    _cache: dict = {}
    _cache_ttl = 1800  # 30 minutes cache

    SPORT_KEYS = {
        "premier_league": "soccer_epl",
        "la_liga": "soccer_spain_la_liga",
        "bundesliga": "soccer_germany_bundesliga",
        "serie_a": "soccer_italy_serie_a",
        "ligue_1": "soccer_france_ligue_one",
        "champions_league": "soccer_uefa_champs_league",
        "mls": "soccer_usa_mls",
        "nba": "basketball_nba",
        "nhl": "icehockey_nhl",
        "ipl": "cricket_ipl",
        "world_cup": "soccer_fifa_world_cup",
        "intl_friendly": "soccer_fifa_world_cup_qualifiers_uefa",  # adjust as needed
    }

    async def get_odds(self, sport: str, regions: str = "uk") -> list:
        if not settings.ODDS_API_KEY:
            return []

        sport_key = self.SPORT_KEYS.get(sport)
        if not sport_key:
            return []

        # Check cache first
        cache_key = f"{sport_key}_{regions}"
        if cache_key in self._cache:
            cached_time, cached_data = self._cache[cache_key]
            if time.time() - cached_time < self._cache_ttl:
                return cached_data

        if not odds_api_quota.can_call():
            # Quota exhausted — return whatever cache we have, fresh or stale
            return self._cache.get(cache_key, (0, []))[1]

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.ODDS_API_BASE}/sports/{sport_key}/odds",
                params={
                    "apiKey": settings.ODDS_API_KEY,
                    "regions": regions,
                    "markets": "h2h,spreads,totals",
                    "oddsFormat": "decimal"
                }
            )
            remaining_hdr = resp.headers.get("x-requests-remaining")
            remaining = int(remaining_hdr) if remaining_hdr and remaining_hdr.isdigit() else None
            odds_api_quota.record(headers_remaining=remaining)
            print(f"Odds API [{sport_key}]: {resp.status_code} | Remaining: {remaining_hdr or '?'}")

            if resp.status_code != 200:
                return self._cache.get(cache_key, (0, []))[1]  # Return stale cache if available

            events = resp.json()
            results = []

            for event in events:
                match_odds = {
                    "id": event.get("id"),
                    "sport": sport,
                    "home_team": event.get("home_team"),
                    "away_team": event.get("away_team"),
                    "commence_time": event.get("commence_time"),
                    "bookmakers": []
                }

                for bookmaker in event.get("bookmakers", []):
                    bm = {
                        "name": bookmaker.get("title"),
                        "markets": {}
                    }
                    for market in bookmaker.get("markets", []):
                        outcomes = {}
                        for outcome in market.get("outcomes", []):
                            outcomes[outcome["name"]] = outcome["price"]
                        bm["markets"][market["key"]] = outcomes
                    match_odds["bookmakers"].append(bm)

                # Calculate average odds across bookmakers
                h2h_odds = {}
                count = 0
                for bm in match_odds["bookmakers"]:
                    h2h = bm["markets"].get("h2h", {})
                    if h2h:
                        count += 1
                        for team, odds in h2h.items():
                            h2h_odds[team] = h2h_odds.get(team, 0) + odds

                if count > 0:
                    match_odds["avg_odds"] = {
                        team: round(total / count, 2)
                        for team, total in h2h_odds.items()
                    }

                results.append(match_odds)

            # Cache results
            self._cache[cache_key] = (time.time(), results)
            return results

    async def get_upcoming_events(self, sport: str) -> list:
        if not settings.ODDS_API_KEY:
            return []

        sport_key = self.SPORT_KEYS.get(sport)
        if not sport_key:
            return []

        if not odds_api_quota.can_call():
            return []
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.ODDS_API_BASE}/sports/{sport_key}/events",
                params={"apiKey": settings.ODDS_API_KEY}
            )
            remaining_hdr = resp.headers.get("x-requests-remaining")
            remaining = int(remaining_hdr) if remaining_hdr and remaining_hdr.isdigit() else None
            odds_api_quota.record(headers_remaining=remaining)
            if resp.status_code != 200:
                return []
            return resp.json()

odds_service = OddsService()
