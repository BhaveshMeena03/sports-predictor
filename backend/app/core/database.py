import aiosqlite
import os
from contextlib import asynccontextmanager

# DATA_DIR points at the persistent volume in deployment (see Dockerfile).
# Unset locally -> the repo-root sports_predictor.db, exactly as before.
# The database is the product's memory: wc_match_log / club_match_log hold
# live pre-kickoff predictions that cannot be regenerated, so in production
# this MUST resolve to mounted storage, not the container filesystem.
_default_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
DB_PATH = os.path.join(os.getenv("DATA_DIR", _default_dir), "sports_predictor.db")

@asynccontextmanager
async def connect(path: str | None = None):
    """Open a connection with sane concurrency behaviour.

    Every open sets busy_timeout: the scheduler and request handlers write
    concurrently on the live server, and SQLite's default is to fail a
    contended write INSTANTLY with "database is locked" instead of waiting.
    Takes the caller's path so tests can monkeypatch a module's DB_PATH.
    """
    async with aiosqlite.connect(path or DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        yield db


async def get_db():
    async with connect() as db:
        db.row_factory = aiosqlite.Row
        yield db

async def init_db():
    async with connect() as db:
        # WAL is persistent (a property of the DB file, set once): readers
        # stop blocking the writer and vice versa, which "delete" mode does
        # on every single write. NORMAL sync is the standard WAL pairing.
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT,
                sport TEXT NOT NULL,
                league TEXT,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                match_date TEXT,
                prediction TEXT NOT NULL,
                confidence REAL,
                odds REAL,
                reasoning TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id INTEGER,
                bet_type TEXT NOT NULL,
                pick TEXT NOT NULL,
                odds REAL NOT NULL,
                stake REAL,
                potential_payout REAL,
                result TEXT DEFAULT 'pending',
                actual_score TEXT,
                profit_loss REAL DEFAULT 0,
                placed_at TEXT DEFAULT (datetime('now')),
                settled_at TEXT,
                FOREIGN KEY (prediction_id) REFERENCES predictions(id)
            );

            CREATE TABLE IF NOT EXISTS multi_bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                total_odds REAL,
                total_stake REAL,
                potential_payout REAL,
                combined_probability REAL,
                result TEXT DEFAULT 'pending',
                profit_loss REAL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                settled_at TEXT
            );

            CREATE TABLE IF NOT EXISTS multi_bet_legs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                multi_bet_id INTEGER NOT NULL,
                bet_id INTEGER NOT NULL,
                leg_order INTEGER,
                FOREIGN KEY (multi_bet_id) REFERENCES multi_bets(id),
                FOREIGN KEY (bet_id) REFERENCES bets(id)
            );

            CREATE TABLE IF NOT EXISTS match_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sport TEXT NOT NULL,
                match_key TEXT UNIQUE NOT NULL,
                data TEXT NOT NULL,
                fetched_at TEXT DEFAULT (datetime('now'))
            );
        """)
        await db.commit()
