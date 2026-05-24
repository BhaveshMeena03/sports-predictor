import asyncio
import logging
import time
import httpx
import json
from datetime import datetime, timedelta
from app.core.config import settings
from app.services.quota import api_football_quota

log = logging.getLogger(__name__)


class FootballService:
    """Fetches football data from API-Football and ESPN."""

    # Simple in-process caches with TTL — protects our 100/day API-Football quota
    _team_id_cache: dict = {}       # name -> (timestamp, team_id)
    _form_cache: dict = {}          # team_id -> (timestamp, dict)
    _h2h_cache: dict = {}           # (id1, id2) -> (timestamp, list)
    _injuries_cache: dict = {}      # team_id -> (timestamp, list)
    _standings_cache: dict = {}     # league_id -> (timestamp, list)
    _CACHE_TTL_SHORT = 1800         # 30 minutes (form/H2H/injuries change slowly)
    _CACHE_TTL_LONG = 21600         # 6 hours (team IDs / standings barely change)

    def __init__(self):
        self.api_football_headers = {
            "x-apisports-key": settings.API_FOOTBALL_KEY
        }

    @staticmethod
    def _cache_get(cache: dict, key, ttl: int):
        entry = cache.get(key)
        if entry and (time.time() - entry[0]) < ttl:
            return entry[1]
        return None

    @staticmethod
    def _cache_set(cache: dict, key, value):
        cache[key] = (time.time(), value)

    async def get_fixtures(self, league_id: int, date: str = None) -> list:
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        # Try API-Football first
        if settings.API_FOOTBALL_KEY:
            return await self._get_fixtures_api_football(league_id, date)

        # Fallback to ESPN (free, no key)
        return await self._get_fixtures_espn(league_id, date)

    async def _get_fixtures_api_football(self, league_id: int, date: str) -> list:
        if not api_football_quota.can_call():
            return []
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.API_FOOTBALL_BASE}/fixtures",
                headers=self.api_football_headers,
                params={"league": league_id, "date": date, "season": settings.API_FOOTBALL_SEASON}
            )
            api_football_quota.record()
            data = resp.json()
            return data.get("response", [])

    async def _get_fixtures_espn(self, league_id: int, date: str) -> list:
        league_map = {
            39: "eng.1",    # Premier League
            140: "esp.1",   # La Liga
            78: "ger.1",    # Bundesliga
            135: "ita.1",   # Serie A
            61: "fra.1",    # Ligue 1
            2: "uefa.champions",  # Champions League
            253: "usa.1",   # MLS
        }
        espn_league = league_map.get(league_id, "eng.1")
        clean_date = date.replace("-", "")

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.ESPN_BASE}/soccer/{espn_league}/scoreboard",
                params={"dates": clean_date}
            )
            data = resp.json()
            events = data.get("events", [])

            fixtures = []
            for event in events:
                competitions = event.get("competitions", [{}])
                comp = competitions[0] if competitions else {}
                competitors = comp.get("competitors", [])

                home = next((c for c in competitors if c.get("homeAway") == "home"), {})
                away = next((c for c in competitors if c.get("homeAway") == "away"), {})

                fixtures.append({
                    "id": event.get("id"),
                    "date": event.get("date"),
                    "status": event.get("status", {}).get("type", {}).get("name"),
                    "home_team": home.get("team", {}).get("displayName", ""),
                    "away_team": away.get("team", {}).get("displayName", ""),
                    "home_score": home.get("score"),
                    "away_score": away.get("score"),
                    "venue": comp.get("venue", {}).get("fullName", ""),
                })
            return fixtures

    async def find_team_id(self, name: str) -> int | None:
        """Look up team ID by name. Cached to save quota."""
        if not settings.API_FOOTBALL_KEY or not name:
            return None
        key = name.lower().strip()
        cached = self._cache_get(self._team_id_cache, key, self._CACHE_TTL_LONG)
        if cached is not None:
            return cached

        if not api_football_quota.can_call():
            return None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{settings.API_FOOTBALL_BASE}/teams",
                    headers=self.api_football_headers,
                    params={"search": name},
                )
                api_football_quota.record()
                data = resp.json()
                teams = data.get("response", [])
                team_id = teams[0]["team"]["id"] if teams else None
                self._cache_set(self._team_id_cache, key, team_id)
                return team_id
        except Exception as e:
            log.warning("find_team_id(%s) failed: %s", name, e)
            return None

    async def hydrate_match(
        self,
        home_team: str,
        away_team: str,
        league_id: int | None = None,
    ) -> dict:
        """One-shot context fetch: team IDs → form + H2H + injuries + standings,
        all in parallel. Returns a dict ready to merge into match_data."""
        out: dict = {}
        if not settings.API_FOOTBALL_KEY:
            log.info("hydrate_match: API_FOOTBALL_KEY missing, skipping")
            return out

        home_id, away_id = await asyncio.gather(
            self.find_team_id(home_team),
            self.find_team_id(away_team),
        )
        out["home_team_id"] = home_id
        out["away_team_id"] = away_id

        tasks: dict[str, asyncio.Task] = {}
        if home_id:
            tasks["home_form"] = asyncio.create_task(self.get_team_form(home_id))
            tasks["home_injuries"] = asyncio.create_task(self.get_injuries(home_id))
        if away_id:
            tasks["away_form"] = asyncio.create_task(self.get_team_form(away_id))
            tasks["away_injuries"] = asyncio.create_task(self.get_injuries(away_id))
        if home_id and away_id:
            tasks["h2h"] = asyncio.create_task(self.get_h2h(home_id, away_id))
        if league_id:
            tasks["standings"] = asyncio.create_task(self.get_standings(league_id))

        if not tasks:
            return out

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for key, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                log.warning("hydrate_match[%s] failed: %s", key, result)
                continue
            # Trim standings to top 10 for prompt size
            if key == "standings" and isinstance(result, list):
                result = result[:10]
            # Trim H2H to last 5 with just the essentials
            if key == "h2h" and isinstance(result, list):
                result = [{
                    "date": m["fixture"]["date"][:10],
                    "home": m["teams"]["home"]["name"],
                    "away": m["teams"]["away"]["name"],
                    "score": f'{m["goals"]["home"]}-{m["goals"]["away"]}',
                } for m in result[:5]]
            out[key] = result
        return out

    async def get_team_form(self, team_id: int, last_n: int = 5) -> dict:
        if not settings.API_FOOTBALL_KEY:
            return {"error": "API-Football key required for team form"}
        cached = self._cache_get(self._form_cache, (team_id, last_n), self._CACHE_TTL_SHORT)
        if cached is not None:
            return cached
        if not api_football_quota.can_call():
            return {"form": "", "wins": 0, "draws": 0, "losses": 0, "matches": [], "quota_exhausted": True}

        async with httpx.AsyncClient(timeout=10.0) as client:
            # Free tier doesn't allow `last` — fetch the whole season and slice client-side
            resp = await client.get(
                f"{settings.API_FOOTBALL_BASE}/fixtures",
                headers=self.api_football_headers,
                params={"team": team_id, "season": settings.API_FOOTBALL_SEASON},
            )
            api_football_quota.record()
            data = resp.json()
            all_matches = data.get("response", [])
            # Only finished matches, most recent first
            finished = [m for m in all_matches if m.get("fixture", {}).get("status", {}).get("short") in ("FT", "AET", "PEN")]
            finished.sort(key=lambda m: m["fixture"]["date"], reverse=True)
            matches = finished[:last_n]

            results = []
            for m in matches:
                home = m["teams"]["home"]
                away = m["teams"]["away"]
                is_home = home["id"] == team_id
                team_goals = m["goals"]["home"] if is_home else m["goals"]["away"]
                opp_goals = m["goals"]["away"] if is_home else m["goals"]["home"]

                if team_goals > opp_goals:
                    result = "W"
                elif team_goals < opp_goals:
                    result = "L"
                else:
                    result = "D"

                results.append({
                    "date": m["fixture"]["date"],
                    "opponent": away["name"] if is_home else home["name"],
                    "home_away": "H" if is_home else "A",
                    "score": f"{m['goals']['home']}-{m['goals']['away']}",
                    "result": result
                })

            form_string = "".join([r["result"] for r in results])
            wins = form_string.count("W")
            draws = form_string.count("D")
            losses = form_string.count("L")

            out = {
                "team_id": team_id,
                "last_n": last_n,
                "form": form_string,
                "wins": wins,
                "draws": draws,
                "losses": losses,
                "matches": results
            }
            self._cache_set(self._form_cache, (team_id, last_n), out)
            return out

    async def get_h2h(self, team1_id: int, team2_id: int) -> list:
        if not settings.API_FOOTBALL_KEY:
            return []
        key = tuple(sorted([team1_id, team2_id]))
        cached = self._cache_get(self._h2h_cache, key, self._CACHE_TTL_SHORT)
        if cached is not None:
            return cached
        if not api_football_quota.can_call():
            return []

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.API_FOOTBALL_BASE}/fixtures/headtohead",
                headers=self.api_football_headers,
                # Free tier blocks `last` — request all and slice
                params={"h2h": f"{team1_id}-{team2_id}"}
            )
            api_football_quota.record()
            data = resp.json()
            h2h = data.get("response", [])
            # Most recent first, top 10
            h2h.sort(key=lambda m: m["fixture"]["date"], reverse=True)
            out = h2h[:10]
            self._cache_set(self._h2h_cache, key, out)
            return out

    async def get_injuries(self, team_id: int) -> list:
        if not settings.API_FOOTBALL_KEY:
            return []
        cached = self._cache_get(self._injuries_cache, team_id, self._CACHE_TTL_SHORT)
        if cached is not None:
            return cached
        if not api_football_quota.can_call():
            return []

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.API_FOOTBALL_BASE}/injuries",
                headers=self.api_football_headers,
                params={"team": team_id, "season": settings.API_FOOTBALL_SEASON}
            )
            api_football_quota.record()
            data = resp.json()
            injuries = data.get("response", [])

            out = [{
                "player": inj["player"]["name"],
                "type": inj["player"]["type"],
                "reason": inj["player"]["reason"],
            } for inj in injuries[:15]]
            self._cache_set(self._injuries_cache, team_id, out)
            return out

    async def get_standings(self, league_id: int) -> list:
        if not settings.API_FOOTBALL_KEY:
            return []
        cached = self._cache_get(self._standings_cache, league_id, self._CACHE_TTL_LONG)
        if cached is not None:
            return cached
        if not api_football_quota.can_call():
            return []

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.API_FOOTBALL_BASE}/standings",
                headers=self.api_football_headers,
                params={"league": league_id, "season": settings.API_FOOTBALL_SEASON}
            )
            api_football_quota.record()
            data = resp.json()
            standings = data.get("response", [])
            out = standings[0].get("league", {}).get("standings", [[]])[0] if standings else []
            self._cache_set(self._standings_cache, league_id, out)
            return out


LEAGUE_IDS = {
    "premier_league": 39,
    "la_liga": 140,
    "bundesliga": 78,
    "serie_a": 135,
    "ligue_1": 61,
    "champions_league": 2,
    "mls": 253,
    "fa_cup": 45,
}

football_service = FootballService()
