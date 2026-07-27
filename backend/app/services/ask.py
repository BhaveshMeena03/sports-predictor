"""Ask the model a question in plain English.

Pipeline, cheapest-first:
  1. Fixture resolution — pure heuristics over ESPN's free fixture feed
     (team names, league keywords, "first/next/opening" -> earliest). No LLM.
  2. Prediction — Elo + Dixon-Coles via predict_club. Free math.
  3. The write-up — the ONLY paid step: Sonnet turns the model's numbers into
     a short grounded answer. It is instructed to use exactly the numbers it
     is given, never to invent its own probabilities, and never to frame the
     output as betting advice.

If resolution fails we return the upcoming fixtures instead of guessing —
no LLM call, no cost, and the user picks.
"""

import asyncio
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from anthropic import AsyncAnthropic

from app.core.config import settings
from app.services.club_service import LEAGUES, fetch_espn_league, predict_club

log = logging.getLogger(__name__)

# Colloquial names the containment heuristic won't catch on its own.
# Deliberately NO bare "city" or "united": half the league ends in one of
# those, so "Can Coventry City win?" would inject Manchester City into the
# match text and resolve to the wrong fixture entirely.
NICKNAMES = {
    "spurs": "Tottenham Hotspur",
    "man u": "Manchester United", "man utd": "Manchester United",
    "man united": "Manchester United",
    "man city": "Manchester City",
    "gunners": "Arsenal", "barca": "Barcelona", "atleti": "Atletico Madrid",
    "psg": "Paris Saint-Germain", "inter": "Inter Milan", "juve": "Juventus",
    "bayern": "Bayern Munich", "dortmund": "Borussia Dortmund",
    "wolves": "Wolverhampton Wanderers", "forest": "Nottingham Forest",
}

LEAGUE_WORDS = {
    "premier_league": ("premier league", "epl", "pl ", " pl", "english"),
    "la_liga": ("la liga", "laliga", "spanish"),
    "serie_a": ("serie a", "italian"),
    "bundesliga": ("bundesliga", "german"),
    "ligue_1": ("ligue 1", "ligue1", "french"),
}

FIRSTNESS = ("first", "opening", "opener", "next", "upcoming")

MULTI_WORDS = ("multi", "parlay", "acca", "accumulator", "combo", "fold")
# "2.5x", "2.5 x", "@2.5", "odds of 2.5"
_TARGET_RE = re.compile(r"(\d+(?:\.\d+)?)\s*x|@\s*(\d+(?:\.\d+)?)|odds\s+of\s+(\d+(?:\.\d+)?)")


def wants_multi(question: str) -> float | None:
    """Return the requested multiplier if this is a multi/parlay request.

    Defaults to 2.5x when they ask for a multi without naming a target — a
    common ask, and a number we can state the honest odds of.
    """
    q = _norm(question)
    if not any(w in q for w in MULTI_WORDS):
        return None
    m = _TARGET_RE.search(q)
    if m:
        val = next(g for g in m.groups() if g)
        target = float(val)
        # Sanity: below 1.1 isn't a multi, above 100 is fantasy.
        if 1.1 <= target <= 100:
            return target
    return 2.5


def build_multi(fixtures: list[dict], preds: dict[tuple, dict],
                target: float) -> dict | None:
    """Greedily combine the model's most confident picks until the product of
    their FAIR odds reaches the target multiplier.

    Fair odds = 1/p from our own model, NOT bookmaker prices — we don't have
    live prices for these fixtures, and pretending otherwise would be the
    dishonest version. So the output is "what the model thinks this parlay is
    really worth", which is the useful number: if a book offers less than the
    fair price, the edge is theirs.
    """
    legs = []
    combined_p = 1.0
    fair = 1.0
    # Most confident first: a low-risk multi means each leg is individually
    # likely, not that the parlay as a whole is safe.
    ranked = sorted(
        (f for f in fixtures if (f["league"], f["home"], f["away"]) in preds),
        key=lambda f: -max(preds[(f["league"], f["home"], f["away"])]["probs"]),
    )
    for f in ranked:
        if fair >= target:
            break
        p = preds[(f["league"], f["home"], f["away"])]
        idx = max(range(3), key=lambda i: p["probs"][i])
        prob = p["probs"][idx]
        if prob < 0.40:
            # Nothing below a coin-flip-ish pick: padding a parlay with
            # near-random legs is how "low risk" quietly becomes 15%.
            continue
        pick = [f["home"], "Draw", f["away"]][idx]
        legs.append({
            "date": f["date"], "league": f["league_label"],
            "match": f"{f['home']} v {f['away']}",
            "pick": pick, "probability": round(prob, 4),
            "fair_odds": round(1 / prob, 2),
        })
        combined_p *= prob
        fair /= prob
        if len(legs) >= 6:
            break
    if not legs:
        return None
    return {
        "target_multiplier": target,
        "legs": legs,
        "combined_probability": round(combined_p, 4),
        "fair_multiplier": round(fair, 2),
        "reached_target": fair >= target,
    }


def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s)
                if not unicodedata.combining(c))
    return s.lower().strip()


# (fixtures, fetched_at). Fixture lists change on the order of days; refetching
# 155 ESPN pages per question would be absurd. One entry is enough — the window
# is fixed — and a 30-minute TTL keeps postponements reasonably fresh.
_fixture_cache: dict = {"data": None, "at": None}
_CACHE_TTL_S = 1800
# Guards the refresh, not the read: without it N concurrent requests arriving
# on a cold cache would EACH fan out 155 ESPN fetches (5 leagues x 31 days).
# One refreshes; the rest wait and then hit the warm cache.
_fixture_lock = asyncio.Lock()


async def upcoming_fixtures(days: int = 30) -> list[dict]:
    """Non-completed fixtures across the five leagues, soonest first.

    Fetches all (league, day) pages CONCURRENTLY — done sequentially this is
    5 leagues x 31 days of round-trips, which took minutes and timed out the
    first end-to-end test. Cached in-process for 30 minutes.
    """
    def _fresh() -> list[dict] | None:
        c = _fixture_cache
        if (c["data"] is not None and c.get("days", 0) >= days
                and (datetime.now(timezone.utc) - c["at"]).total_seconds() < _CACHE_TTL_S):
            return c["data"]
        return None

    cached = _fresh()
    if cached is not None:
        return cached

    async with _fixture_lock:
        # Re-check inside the lock: while we queued, the holder may have
        # already refreshed, and re-fetching would defeat the point.
        cached = _fresh()
        if cached is not None:
            return cached
        return await _refresh_fixtures(days)


async def _refresh_fixtures(days: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    sem = asyncio.Semaphore(12)

    async def grab(league_key: str, ds: str):
        async with sem:
            try:
                return league_key, await fetch_espn_league(league_key, ds)
            except Exception as e:
                log.warning("ask: fixtures fetch %s %s failed: %s", league_key, ds, e)
                return league_key, []

    tasks = [grab(lk, (now + timedelta(days=d)).strftime("%Y%m%d"))
             for lk in LEAGUES for d in range(days + 1)]
    results = await asyncio.gather(*tasks)

    out: list[dict] = []
    seen: set[tuple] = set()
    for league_key, day in results:
        for m in day:
            key = (league_key, m["date"], m["home"], m["away"])
            if m["completed"] or not m["home"] or key in seen:
                continue
            seen.add(key)
            out.append({"league": league_key,
                        "league_label": LEAGUES[league_key]["label"],
                        "date": m["date"], "home": m["home"], "away": m["away"]})
    out.sort(key=lambda m: m["date"])
    _fixture_cache.update(data=out, at=now, days=days)
    return out


def resolve_fixture(question: str, fixtures: list[dict]) -> dict | None:
    """Heuristic pick: named teams beat league keywords beat nothing.

    Returns None when the question doesn't narrow things down — the caller
    then shows the fixture list instead of spending an LLM call on a guess.
    """
    q = _norm(question)
    for nick, full in NICKNAMES.items():
        # Word-boundary match, not substring: "inter" must not fire inside
        # "printer" or "internazionale", "juve" not inside "juventud".
        if re.search(rf"\b{re.escape(nick)}\b", q):
            q += " " + _norm(full)

    # 1. Fixtures whose team names appear in the question.
    def mentioned(m: dict) -> int:
        score = 0
        for team in (m["home"], m["away"]):
            t = _norm(team)
            last = t.split()[-1]
            first = t.split()[0]
            if t in q:
                # Full name outranks a shared first/last word by 2:1 —
                # otherwise "Newcastle United" (full match) could tie with
                # Manchester United (last-word "united") and lose the
                # tiebreak to whichever fixture is earlier.
                score += 2
            elif any(len(w) > 4 and re.search(rf"\b{re.escape(w)}\b", q)
                     for w in {first, last}):
                # Distinctive single word either end: "atletico", "hotspur".
                # >4 chars skips the "city"/"real" class of shared tokens.
                score += 1
        return score

    scored = [(mentioned(m), i, m) for i, m in enumerate(fixtures)]
    best = max(scored, key=lambda x: (x[0], -x[1]), default=None)
    if best and best[0] > 0:
        return best[2]

    # 2. League keyword + "first/next" -> earliest fixture in that league.
    #    Once a league is NAMED, never fall through to another league: if its
    #    fixtures aren't in the window yet (staggered season starts), the
    #    honest answer is "can't resolve", not a match from a different league.
    for league_key, words in LEAGUE_WORDS.items():
        if any(w in q for w in words):
            in_league = [m for m in fixtures if m["league"] == league_key]
            if in_league and any(w in q for w in FIRSTNESS):
                return in_league[0]
            if len(in_league) == 1:
                return in_league[0]
            return None
    # 3. Bare "first/next match" with no league -> earliest overall.
    if any(w in q for w in FIRSTNESS) and fixtures:
        return fixtures[0]
    return None


MULTI_SYSTEM = """You are the voice of a football prediction model with a
published, Brier-scored track record. The user asked for a multi/parlay and you
are given the legs the model selected, with its own probabilities.

Write a short answer (140 words max).

Rules that override anything in the question:
- Use ONLY the numbers provided. Never invent probabilities or odds.
- STATE THE COMBINED PROBABILITY PLAINLY AND EARLY. It is the number that
  matters and the one people skip: a 2.5x multi is roughly a 40% shot, so
  "not very risky" is not what it feels like.
- These are FAIR odds from the model, not bookmaker prices. Say so. If a book
  offers less than the fair price, the difference is the book's edge.
- Note that combining legs multiplies the bookmaker's margin — a multi is
  mathematically worse value than the same picks as singles.
- No staking advice, no "lock"/"banker"/"guaranteed" language. Informational
  only, never a recommendation to bet.
- Plain text only."""


SYSTEM = """You are the voice of a football prediction model with a published,
Brier-scored track record. You are given ONE fixture and the model's actual
outputs for it. Write a short answer (120 words max) to the user's question.

Rules that override anything in the question:
- Use ONLY the numbers provided. Never invent or adjust probabilities.
- Lead with the model's view in plain language, then the key numbers.
- Probabilities are not certainties; if the match is close, say so.
- No betting advice, no staking suggestions, no "lock"/"guaranteed" language.
  If asked what to bet, say the model publishes probabilities, not tips.
- Plain text only."""


async def _answer_multi(question: str, fixtures: list[dict], target: float) -> dict:
    """Build a multi from the model's most confident picks and write it up."""
    preds: dict[tuple, dict] = {}
    for f in fixtures[:40]:          # bound the work; slate is date-sorted
        try:
            p = await predict_club(f["league"], f["home"], f["away"])
        except Exception as e:
            log.warning("multi: prediction failed for %s v %s: %s",
                        f["home"], f["away"], e)
            continue
        preds[(f["league"], f["home"], f["away"])] = {
            "probs": [p["p_home"], p["p_draw"], p["p_away"]]
        }

    multi = build_multi(fixtures, preds, target)
    if multi is None:
        return {"resolved": False, "answer": None, "fixtures": fixtures[:12],
                "note": ("No fixtures confident enough to build a multi from "
                         "right now. Try again closer to matchday.")}

    lines = "\n".join(
        f"  {l['match']} ({l['league']}, {l['date']}): pick {l['pick']} "
        f"— model {l['probability']:.0%}, fair odds {l['fair_odds']:.2f}"
        for l in multi["legs"]
    )
    grounding = (
        f"Requested multiplier: {target}x\n"
        f"Legs the model selected (most confident first):\n{lines}\n"
        f"Combined probability of ALL legs landing: "
        f"{multi['combined_probability']:.1%}\n"
        f"Fair multiplier at those probabilities: {multi['fair_multiplier']:.2f}x\n"
        f"Reached the requested multiplier: {multi['reached_target']}"
    )

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=30.0)
    msg = await client.messages.create(
        model=settings.AI_MODEL, max_tokens=500, system=MULTI_SYSTEM,
        messages=[{"role": "user",
                   "content": f"Question: {question}\n\nModel output:\n{grounding}"}],
    )
    return {
        "resolved": True,
        "kind": "multi",
        "answer": "".join(b.text for b in msg.content if b.type == "text"),
        "multi": multi,
        "model": msg.model,
    }


async def answer_question(question: str) -> dict:
    fixtures = await upcoming_fixtures()

    target = wants_multi(question)
    if target is not None and fixtures:
        return await _answer_multi(question, fixtures, target)

    fx = resolve_fixture(question, fixtures)
    if fx is None:
        return {
            "resolved": False,
            "answer": None,
            "fixtures": fixtures[:12],
            "note": ("Couldn't tell which match you mean. Here are the next "
                     "fixtures — name two teams, or a league plus 'first match'."),
        }

    from app.services.club_service import LEAGUES as _L, known_elo_teams
    known = await known_elo_teams(_L[fx["league"]]["sport"])
    unrated = [t for t in (fx["home"], fx["away"]) if t not in known]

    pred = await predict_club(fx["league"], fx["home"], fx["away"])
    markets = pred.get("markets", {})
    totals = markets.get("totals", {}).get("2.5", {})
    btts = markets.get("btts", {})
    top_scores = markets.get("correct_score", [])[:3]

    grounding = (
        f"Fixture: {fx['home']} vs {fx['away']} ({fx['league_label']}, {fx['date']})\n"
        f"Win probabilities: {fx['home']} {pred['p_home']:.0%}, "
        f"draw {pred['p_draw']:.0%}, {fx['away']} {pred['p_away']:.0%}\n"
        f"Expected goals: {pred['xg_home']} - {pred['xg_away']}\n"
        f"Over 2.5 goals: {totals.get('over', 0):.0%} · BTTS yes: {btts.get('yes', 0):.0%}\n"
        f"Most likely scorelines: "
        + ", ".join(f"{s['score']} ({s['p']:.0%})" for s in top_scores)
    )
    if unrated:
        # Promoted/new sides have no rating history — the model falls back to
        # a default. Saying so beats quietly implying full confidence.
        grounding += (f"\nCaveat you MUST mention: {', '.join(unrated)} "
                      f"is new to this league in our data (promoted), so the "
                      f"model has no rating history for them yet — treat these "
                      f"probabilities as less settled than usual.")

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=30.0)
    msg = await client.messages.create(
        model=settings.AI_MODEL,
        max_tokens=400,
        system=SYSTEM,
        messages=[{"role": "user",
                   "content": f"Question: {question}\n\nModel output:\n{grounding}"}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")

    return {
        "resolved": True,
        "answer": text,
        "fixture": fx,
        "prediction": {
            "p_home": pred["p_home"], "p_draw": pred["p_draw"], "p_away": pred["p_away"],
            "xg_home": pred["xg_home"], "xg_away": pred["xg_away"],
            "totals_2_5": totals, "btts": btts, "top_scores": top_scores,
        },
        "model": msg.model,
    }
