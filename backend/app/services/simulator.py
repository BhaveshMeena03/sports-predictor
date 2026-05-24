"""
Monte Carlo World Cup simulator.

Simulates the full tournament thousands of times using national-team Elo to
produce probabilities the betting market prices as 'outright' / 'futures' markets
(win the cup, reach the final, advance from group). These futures markets are
SOFTER than single-match lines — the best realistic edge we identified.

2026 format: 48 teams, 12 groups of 4. Top 2 of each group + 8 best 3rd-placed
teams = 32 → single-elimination knockout (R32 → R16 → QF → SF → Final).

Match model: neutral-venue Elo win/draw/away probabilities, sampled. Knockout
draws go to a coin-flip weighted by relative Elo (penalty-shootout proxy).
"""

import logging
import random
from collections import defaultdict
from app.services.elo import EloRatings, DEFAULT_RATING

log = logging.getLogger(__name__)
SPORT = "international"


def _win_prob_no_draw(rating_a: float, rating_b: float) -> float:
    """Two-way win probability (used for knockout shootouts)."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def _sample_group_match(ra: float, rb: float, rng: random.Random) -> tuple[int, int]:
    """Return (points_a, points_b) from one group match using Elo.
    Draw probability scales with how close the teams are."""
    p_a = _win_prob_no_draw(ra, rb)
    closeness = 1.0 - abs(p_a - 0.5) * 2
    p_draw = 0.26 * closeness
    r = rng.random()
    if r < p_draw:
        return (1, 1)
    # Split remaining mass by relative strength
    if rng.random() < p_a:
        return (3, 0)
    return (0, 3)


def _simulate_group(teams: list[str], ratings: dict[str, float], rng: random.Random) -> list[str]:
    """Round-robin. Return teams ranked best→worst (points, then Elo tiebreak)."""
    points = defaultdict(int)
    for i in range(len(teams)):
        for j in range(i + 1, len(teams)):
            a, b = teams[i], teams[j]
            pa, pb = _sample_group_match(ratings[a], ratings[b], rng)
            points[a] += pa
            points[b] += pb
    # Tiebreak by Elo (proxy for goal difference) plus tiny noise
    return sorted(teams, key=lambda t: (points[t], ratings[t], rng.random()), reverse=True)


def _knockout_winner(a: str, b: str, ratings: dict[str, float], rng: random.Random) -> str:
    """Single match, no draws (shootout proxy weighted by Elo)."""
    return a if rng.random() < _win_prob_no_draw(ratings[a], ratings[b]) else b


def _simulate_knockout(qualifiers: list[str], ratings: dict[str, float],
                       rng: random.Random, round_tracker: dict) -> str:
    """Single elimination from a seeded list. Tracks how far each team gets."""
    bracket = qualifiers[:]
    # Pad to a power of two with byes if needed
    rng.shuffle(bracket)
    round_names = {32: "R32", 16: "R16", 8: "QF", 4: "SF", 2: "Final"}
    while len(bracket) > 1:
        size = len(bracket)
        label = round_names.get(size, f"R{size}")
        for t in bracket:
            round_tracker[t][label] += 1
        winners = []
        for i in range(0, len(bracket), 2):
            if i + 1 >= len(bracket):
                winners.append(bracket[i])  # bye
                continue
            winners.append(_knockout_winner(bracket[i], bracket[i + 1], ratings, rng))
        bracket = winners
    champion = bracket[0]
    round_tracker[champion]["Champion"] += 1
    return champion


def auto_groups(ratings: dict[str, float], n_groups: int = 12, per_group: int = 4) -> dict[str, list[str]]:
    """Snake-seed the top (n_groups*per_group) teams into balanced groups.
    Used when the real draw isn't supplied."""
    ranked = sorted(ratings, key=lambda t: ratings[t], reverse=True)[: n_groups * per_group]
    groups = {chr(65 + g): [] for g in range(n_groups)}
    keys = list(groups.keys())
    idx = 0
    direction = 1
    for team in ranked:
        groups[keys[idx]].append(team)
        if direction == 1 and idx == n_groups - 1:
            direction = -1
        elif direction == -1 and idx == 0:
            direction = 1
        else:
            idx += direction
    return groups


async def simulate(groups: dict[str, list[str]] = None, n_sims: int = 10000,
                   seed: int = None) -> dict:
    """Run the Monte Carlo. groups: {group_name: [4 teams]}. If None, auto-seed."""
    elo = EloRatings(SPORT)
    all_ratings = {r["team"]: r["rating"] for r in await elo.all_ratings(limit=300)}

    if not groups:
        if not all_ratings:
            return {"error": "No ratings — run /worldcup/bootstrap or /worldcup/ingest first"}
        groups = auto_groups(all_ratings)

    # Ensure every team has a rating (default for unknowns)
    teams = [t for g in groups.values() for t in g]
    ratings = {t: all_ratings.get(t, DEFAULT_RATING) for t in teams}

    rng = random.Random(seed)
    advance = defaultdict(int)       # times team reached knockout
    round_tracker = defaultdict(lambda: defaultdict(int))

    for _ in range(n_sims):
        group_winners, group_runners, third_place = [], [], []
        for gname, gteams in groups.items():
            ranked = _simulate_group(gteams, ratings, rng)
            group_winners.append(ranked[0])
            group_runners.append(ranked[1])
            if len(ranked) >= 3:
                third_place.append(ranked[2])
        # Best 8 third-placed (by Elo proxy)
        best_thirds = sorted(third_place, key=lambda t: ratings[t], reverse=True)[:8]
        qualifiers = group_winners + group_runners + best_thirds
        for t in qualifiers:
            advance[t] += 1
        _simulate_knockout(qualifiers, ratings, rng, round_tracker)

    # Aggregate
    def pct(n):
        return round(n / n_sims * 100, 1)

    results = []
    for t in teams:
        rt = round_tracker[t]
        results.append({
            "team": t,
            "elo": round(ratings[t], 1),
            "advance_pct": pct(advance[t]),
            "reach_QF_pct": pct(rt.get("QF", 0)),
            "reach_SF_pct": pct(rt.get("SF", 0)),
            "reach_final_pct": pct(rt.get("Final", 0)),
            "win_pct": pct(rt.get("Champion", 0)),
        })
    results.sort(key=lambda x: x["win_pct"], reverse=True)
    return {
        "n_sims": n_sims,
        "n_teams": len(teams),
        "n_groups": len(groups),
        "groups": groups,
        "results": results,
    }
