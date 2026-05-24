import os
from dotenv import load_dotenv

# override=True so empty env vars (e.g. ANTHROPIC_API_KEY="" set by Claude Desktop on macOS)
# don't silently shadow values in .env
load_dotenv(override=True)

class Settings:
    APP_NAME: str = "Sports Predictor AI"
    VERSION: str = "1.0.0"

    # AI
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    AI_MODEL: str = "claude-haiku-4-5-20251001"  # cheap + fast for analysis

    # Sports Data APIs
    API_FOOTBALL_KEY: str = os.getenv("API_FOOTBALL_KEY", "")
    API_FOOTBALL_BASE: str = "https://v3.football.api-sports.io"
    # Free tier only allows seasons 2022-2024 and blocks the `last=N` param.
    API_FOOTBALL_SEASON: int = int(os.getenv("API_FOOTBALL_SEASON", "2024"))

    ODDS_API_KEY: str = os.getenv("ODDS_API_KEY", "")
    ODDS_API_BASE: str = "https://api.the-odds-api.com/v4"

    CRICKET_API_BASE: str = "https://api.cricapi.com/v1"

    # ESPN (no key needed)
    ESPN_BASE: str = "https://site.api.espn.com/apis/site/v2/sports"

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./sports_predictor.db")

    # Rate limits (free tier)
    API_FOOTBALL_DAILY_LIMIT: int = 100
    ODDS_API_MONTHLY_LIMIT: int = 500

settings = Settings()
