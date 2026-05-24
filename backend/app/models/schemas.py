from pydantic import BaseModel
from typing import Optional

class MatchAnalysisRequest(BaseModel):
    sport: str  # football, nba, nhl, cricket
    league: Optional[str] = None
    home_team: str
    away_team: str
    date: Optional[str] = None
    venue: Optional[str] = None
    extra_context: Optional[str] = None  # user can add info like "Kane is injured"

class MultiBetLeg(BaseModel):
    match: str  # "Arsenal vs Bournemouth"
    pick: str   # "Arsenal Win"
    odds: float # 1.46
    sport: str  # "football"
    confidence: Optional[float] = None

class MultiBetRequest(BaseModel):
    legs: list[MultiBetLeg]
    stake: Optional[float] = None
    bankroll: Optional[float] = None  # if set, Kelly stake is computed

class BetRecord(BaseModel):
    match: str
    bet_type: str  # "1x2", "BTTS", "Over/Under", "Double Chance"
    pick: str
    odds: float
    stake: float
    result: Optional[str] = "pending"  # "won", "lost", "void", "pending"
    actual_score: Optional[str] = None

class QuickAnalysisRequest(BaseModel):
    query: str  # "Is Arsenal vs Bournemouth safe for my multi?"

class FixturesRequest(BaseModel):
    sport: str
    league: Optional[str] = None
    date: Optional[str] = None
