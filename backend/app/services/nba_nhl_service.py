import httpx
from app.core.config import settings

class NBAService:
    """Fetches NBA data from ESPN API (free, no key needed)."""

    async def get_scoreboard(self, date: str = None) -> list:
        params = {}
        if date:
            params["dates"] = date.replace("-", "")

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.ESPN_BASE}/basketball/nba/scoreboard",
                params=params
            )
            data = resp.json()
            events = data.get("events", [])

            games = []
            for event in events:
                comp = event.get("competitions", [{}])[0]
                competitors = comp.get("competitors", [])
                home = next((c for c in competitors if c.get("homeAway") == "home"), {})
                away = next((c for c in competitors if c.get("homeAway") == "away"), {})

                games.append({
                    "id": event.get("id"),
                    "date": event.get("date"),
                    "status": event.get("status", {}).get("type", {}).get("name"),
                    "home_team": home.get("team", {}).get("displayName", ""),
                    "away_team": away.get("team", {}).get("displayName", ""),
                    "home_score": home.get("score"),
                    "away_score": away.get("score"),
                    "home_record": home.get("records", [{}])[0].get("summary", "") if home.get("records") else "",
                    "away_record": away.get("records", [{}])[0].get("summary", "") if away.get("records") else "",
                })
            return games

    async def get_standings(self) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.ESPN_BASE}/basketball/nba/standings"
            )
            data = resp.json()
            standings = {"east": [], "west": []}

            for child in data.get("children", []):
                conf = "east" if "east" in child.get("name", "").lower() else "west"
                for entry in child.get("standings", {}).get("entries", []):
                    team = entry.get("team", {})
                    stats = {s["name"]: s["value"] for s in entry.get("stats", [])}
                    standings[conf].append({
                        "team": team.get("displayName", ""),
                        "wins": int(stats.get("wins", 0)),
                        "losses": int(stats.get("losses", 0)),
                        "pct": stats.get("winPercent", 0),
                    })
            return standings


class NHLService:
    """Fetches NHL data from ESPN API (free, no key needed)."""

    async def get_scoreboard(self, date: str = None) -> list:
        params = {}
        if date:
            params["dates"] = date.replace("-", "")

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.ESPN_BASE}/hockey/nhl/scoreboard",
                params=params
            )
            data = resp.json()
            events = data.get("events", [])

            games = []
            for event in events:
                comp = event.get("competitions", [{}])[0]
                competitors = comp.get("competitors", [])
                home = next((c for c in competitors if c.get("homeAway") == "home"), {})
                away = next((c for c in competitors if c.get("homeAway") == "away"), {})

                games.append({
                    "id": event.get("id"),
                    "date": event.get("date"),
                    "status": event.get("status", {}).get("type", {}).get("name"),
                    "home_team": home.get("team", {}).get("displayName", ""),
                    "away_team": away.get("team", {}).get("displayName", ""),
                    "home_score": home.get("score"),
                    "away_score": away.get("score"),
                    "home_record": home.get("records", [{}])[0].get("summary", "") if home.get("records") else "",
                    "away_record": away.get("records", [{}])[0].get("summary", "") if away.get("records") else "",
                })
            return games

    async def get_standings(self) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.ESPN_BASE}/hockey/nhl/standings"
            )
            data = resp.json()
            divisions = {}

            for child in data.get("children", []):
                for div in child.get("children", []):
                    div_name = div.get("name", "")
                    teams = []
                    for entry in div.get("standings", {}).get("entries", []):
                        team = entry.get("team", {})
                        stats = {s["name"]: s["value"] for s in entry.get("stats", [])}
                        teams.append({
                            "team": team.get("displayName", ""),
                            "wins": int(stats.get("wins", 0)),
                            "losses": int(stats.get("losses", 0)),
                            "points": int(stats.get("points", 0)),
                        })
                    divisions[div_name] = teams
            return divisions


nba_service = NBAService()
nhl_service = NHLService()
