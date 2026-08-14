"""Beat the Model: public picks, scored the way the model is scored.

Why Brier and not win/loss
--------------------------
Every prediction game scores picks right or wrong, which rewards confident
guessing: call ten favourites at 95% and you look brilliant until you don't.
Scoring on Brier rewards being correctly uncertain instead. Saying 55% on a
coin-flip derby and being right beats saying 95% and being right, because the
55% was the better description of the world.

That metric is only meaningful next to a baseline, and this service happens to
have one: the same model, scored the same way, on the same fixtures. That is
the product. The leaderboard is not "who guessed most" but "who beat a
calibrated model at describing uncertainty".

The integrity property
----------------------
A pick is locked at kickoff and never rewritten. This mirrors what the club
prediction log already does -- probabilities are written in advance, and
scoring only fills in the result. Without that, a leaderboard is unfalsifiable
and therefore worthless. The lock is enforced on write (reject after kickoff)
and again on score (the stored vector is what gets scored, never a fresh one).

Free to play, deliberately
--------------------------
No stake, no custody, no payout. That keeps this a game rather than a
sportsbook, which is a regulated activity almost everywhere. Prizes, if any,
are handed out off the back of the leaderboard rather than pooled from
entrants.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.core.database import DB_PATH
from app.core.database import connect as db_connect

log = logging.getLogger(__name__)

OUTCOMES = ("home", "draw", "away")

# A pick is a chosen outcome plus how sure the user is. Three free-floating
# probabilities would be more expressive and nobody would fill them in, so the
# remainder is split evenly across the other two outcomes. Stored as the full
# vector regardless, so scoring never has to re-derive anything.
MIN_CONFIDENCE = 0.34   # below a third is not a pick, it is a different pick
MAX_CONFIDENCE = 0.95   # nothing in football is 99%; the cap is a kindness

# Player names are display strings on a public leaderboard, so they get the
# treatment untrusted display strings need: a fixed alphabet, a length bound,
# and case folding. Without folding, "Alice" and "alice" are two rows; without
# the alphabet, zero-width and right-to-left characters let someone render a
# name identical to another player's.
_NAME_OK = re.compile(r"^[a-z0-9_.\-]{2,32}$")
# A verified wallet address is also a valid player id. It is longer than the
# name limit and always lowercase by the time it reaches here.
_ADDRESS_OK = re.compile(r"^0x[0-9a-f]{40}$")

# Bounded per player so one script cannot fill the table. Well above a season
# of real play: five leagues rarely exceed ~50 fixtures a week.
MAX_PICKS_PER_PLAYER = 2000


async def ensure_tables() -> None:
    async with db_connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_picks (
                player      TEXT NOT NULL,
                league      TEXT NOT NULL,
                date        TEXT NOT NULL,
                home        TEXT NOT NULL,
                away        TEXT NOT NULL,
                pick        TEXT NOT NULL,
                p_home      REAL NOT NULL,
                p_draw      REAL NOT NULL,
                p_away      REAL NOT NULL,
                submitted_at TEXT NOT NULL,
                kickoff_utc  TEXT,
                verified     INTEGER NOT NULL DEFAULT 0,
                signature    TEXT,
                correct     INTEGER,
                brier       REAL,
                PRIMARY KEY (player, league, date, home, away)
            );
        """)
        # Leaderboard reads scan by player; scoring scans by fixture.
        await db.execute("CREATE INDEX IF NOT EXISTS idx_picks_player "
                         "ON user_picks(player)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_picks_fixture "
                         "ON user_picks(league, date, home, away)")

        # CREATE TABLE IF NOT EXISTS does nothing to a table that already
        # exists, so new columns need adding explicitly. The picks table
        # shipped before wallet signing did, and those rows are genuinely
        # unverified -- defaulting to 0 records that rather than flattering it.
        cur = await db.execute("PRAGMA table_info(user_picks)")
        cols = {row[1] for row in await cur.fetchall()}
        if "verified" not in cols:
            await db.execute(
                "ALTER TABLE user_picks ADD COLUMN verified INTEGER NOT NULL DEFAULT 0")
        if "signature" not in cols:
            await db.execute("ALTER TABLE user_picks ADD COLUMN signature TEXT")
        await db.commit()


def normalise_player(name: str) -> str:
    """Fold and validate a display name, or refuse it.

    Case-folded so one person is one row rather than one per capitalisation,
    and restricted to a plain alphabet so nobody can register a name that
    renders identically to someone else's using zero-width joiners or
    right-to-left marks.
    """
    folded = (name or "").strip().lower()
    if _ADDRESS_OK.match(folded):
        return folded
    if not _NAME_OK.match(folded):
        raise ValueError(
            "player must be 2-32 characters, using letters, digits, _ . or -")
    return folded


def vector_from(pick: str, confidence: float) -> list[float]:
    """Turn (outcome, confidence) into a probability vector over H/D/A."""
    if pick not in OUTCOMES:
        raise ValueError(f"pick must be one of {OUTCOMES}")
    c = max(MIN_CONFIDENCE, min(MAX_CONFIDENCE, float(confidence)))
    rest = (1.0 - c) / 2.0
    return [c if o == pick else rest for o in OUTCOMES]


def brier(probs: list[float], outcome_index: int) -> float:
    """Multi-class Brier, averaged over the three outcomes.

    Same formula the model is scored with, so the two numbers are directly
    comparable. Divided by 3 to match; a different divisor would make the
    leaderboard silently incomparable to the track record.
    """
    actual = [0.0, 0.0, 0.0]
    actual[outcome_index] = 1.0
    return sum((probs[i] - actual[i]) ** 2 for i in range(3)) / 3


async def submit_pick(player: str, league: str, date: str, home: str, away: str,
                      pick: str, confidence: float,
                      kickoff_utc: str | None = None,
                      verified: bool = False,
                      signature: str | None = None) -> dict:
    """Record a pick, refusing anything at or after kickoff.

    Refusing late picks is the whole basis of the leaderboard, so the check
    lives here rather than in the route: any future caller gets it too.
    """
    await ensure_tables()
    player = normalise_player(player)
    probs = vector_from(pick, confidence)
    now = datetime.now(timezone.utc)

    if kickoff_utc:
        try:
            ko = datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00"))
            if ko.tzinfo is None:
                ko = ko.replace(tzinfo=timezone.utc)
            if now >= ko:
                raise ValueError("that fixture has already kicked off")
        except ValueError as e:
            if "already kicked off" in str(e):
                raise
            log.warning("unparseable kickoff %r, falling back to date", kickoff_utc)
            kickoff_utc = None

    if not kickoff_utc:
        # No kickoff time available: fall back to the fixture date. Coarser,
        # and stated rather than hidden -- a pick made on match day before the
        # feed reports a time is allowed, which is the forgiving direction.
        if date < now.strftime("%Y-%m-%d"):
            raise ValueError("that fixture is in the past")

    async with db_connect(DB_PATH) as db:
        # The gate that actually protects the leaderboard. Results are ingested
        # the same day a match finishes, and a date comparison only refuses
        # dates strictly before today -- so a player could read a result that
        # landed this morning and submit a "prediction" for it this afternoon.
        # The scorer joins on the fixture and does not care when the pick
        # arrived, so it would have counted.
        #
        # Asking the result table directly is airtight regardless of dates,
        # timezones or how late the feed is.
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='club_match_log'")
        if await cur.fetchone() is not None:
            cur = await db.execute(
                """SELECT home_goals FROM club_match_log
                   WHERE league=? AND date=? AND home=? AND away=?""",
                (league, date, home, away))
            row = await cur.fetchone()
            if row is not None and row[0] is not None:
                raise ValueError("that fixture has already been played")

        cur = await db.execute(
            "SELECT COUNT(*) FROM user_picks WHERE player=?", (player,))
        (count,) = await cur.fetchone()
        if count >= MAX_PICKS_PER_PLAYER:
            raise ValueError("you have reached the maximum number of picks")

        cur = await db.execute(
            """SELECT brier FROM user_picks
               WHERE player=? AND league=? AND date=? AND home=? AND away=?""",
            (player, league, date, home, away))
        existing = await cur.fetchone()
        if existing is not None:
            # Changing a standing pick would make the leaderboard meaningless.
            raise ValueError("you already picked this fixture")
        await db.execute(
            """INSERT INTO user_picks
               (player,league,date,home,away,pick,p_home,p_draw,p_away,
                submitted_at,kickoff_utc,verified,signature)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (player, league, date, home, away, pick, *probs,
             now.isoformat(), kickoff_utc, 1 if verified else 0, signature))
        await db.commit()

    return {"player": player, "fixture": f"{home} v {away}", "date": date,
            "pick": pick, "probabilities": dict(zip(OUTCOMES, probs)),
            "verified": bool(verified),
            "locked_at": kickoff_utc or f"{date} (end of day)"}


async def score_finished_picks() -> dict:
    """Score every unscored pick whose fixture now has a result.

    Reads the result from club_match_log rather than re-fetching it, so user
    picks and the model are always scored against exactly the same outcome.
    """
    await ensure_tables()
    scored = 0
    async with db_connect(DB_PATH) as db:
        # club_match_log is created by club_service, which may not have run yet
        # on a fresh deploy. Picks can legitimately exist before any result
        # does, and the nightly job must not fall over on that ordering.
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='club_match_log'")
        if await cur.fetchone() is None:
            return {"scored": 0, "note": "no club results table yet"}

        rows = await (await db.execute("""
            SELECT p.player, p.league, p.date, p.home, p.away,
                   p.pick, p.p_home, p.p_draw, p.p_away,
                   m.home_goals, m.away_goals
            FROM user_picks p
            JOIN club_match_log m
              ON m.league = p.league AND m.date = p.date
             AND m.home = p.home AND m.away = p.away
            WHERE p.brier IS NULL AND m.home_goals IS NOT NULL
        """)).fetchall()

        for (player, league, date, home, away, pick,
             ph, pd, pa, hg, ag) in rows:
            idx = 0 if hg > ag else (2 if hg < ag else 1)
            b = brier([ph, pd, pa], idx)
            await db.execute(
                """UPDATE user_picks SET correct=?, brier=?
                   WHERE player=? AND league=? AND date=? AND home=? AND away=?""",
                (1 if OUTCOMES[idx] == pick else 0, round(b, 4),
                 player, league, date, home, away))
            scored += 1
        await db.commit()
    return {"scored": scored}


async def leaderboard(limit: int = 50, min_picks: int = 3) -> dict:
    """Standings by average Brier, lower is better, against the model.

    min_picks exists because one lucky pick at 0.95 confidence would otherwise
    top a table of people who have played all season. Anyone below the
    threshold is still tracked, just not ranked.
    """
    await ensure_tables()
    async with db_connect(DB_PATH) as db:
        rows = await (await db.execute("""
            SELECT player, COUNT(*) n, AVG(brier) avg_brier, SUM(correct) hits,
                   MIN(verified) all_verified
            FROM user_picks WHERE brier IS NOT NULL
            GROUP BY player ORDER BY avg_brier ASC
        """)).fetchall()

        # The model's score over the SAME fixtures anyone has picked, so the
        # comparison is like for like rather than against its whole season.
        model = await (await db.execute("""
            SELECT AVG(m.brier), COUNT(*) FROM club_match_log m
            WHERE m.brier IS NOT NULL AND EXISTS (
                SELECT 1 FROM user_picks p
                WHERE p.league=m.league AND p.date=m.date
                  AND p.home=m.home AND p.away=m.away)
        """)).fetchone()

    ranked, unranked = [], []
    for player, n, avg_b, hits, all_verified in rows:
        entry = {"player": player, "picks": n,
                 "avg_brier": round(avg_b, 4),
                 "correct": hits,
                 # Every pick signed by the wallet that owns this row. A name
                 # anyone could have typed is shown, but marked apart.
                 "verified": bool(all_verified),
                 "accuracy": round(hits / n, 3) if n else None}
        (ranked if n >= min_picks else unranked).append(entry)

    model_brier = round(model[0], 4) if model and model[0] is not None else None
    for i, e in enumerate(ranked, 1):
        e["rank"] = i
        e["beats_model"] = (model_brier is not None and e["avg_brier"] < model_brier)

    return {
        "model": {"avg_brier": model_brier,
                  "matches": model[1] if model else 0,
                  "note": "scored over the same fixtures players picked"},
        "min_picks_to_rank": min_picks,
        "ranked": ranked[:limit],
        "unranked": len(unranked),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
