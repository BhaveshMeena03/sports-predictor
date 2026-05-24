import aiosqlite
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "sports_predictor.db")

async def get_db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
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
