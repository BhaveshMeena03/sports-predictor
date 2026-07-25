"""
Draw-aware goals model for international football (predict_v2).

Why: the Elo argmax predictor structurally CANNOT pick a draw (draw share is
capped below the winner's share), and this WC produced 25+ draws it all missed.
This module converts Elo strength into expected GOALS, runs a Poisson grid,
and lets the draw win the argmax when the game is genuinely tight.

Pipeline:
  1. Elo -> two-way win expectation (existing ratings, incl. home adv if any)
  2. Solve the home/away goal-rate split so the Poisson grid's win prob
     matches the Elo expectation (self-consistent bridge, no new data needed)
  3. Total-goals baseline from THIS tournament's observed scoring rate,
     nudged by each team's attack/defense record (heavily shrunk — 4-6 game
     samples get ~30-40% weight, the Egypt/Colombia pattern-fragility lesson)
  4. Apply the fitted calibration layer (calibration_layer.fit) to the vector
"""

import math
import aiosqlite
from app.core.database import DB_PATH
from app.services.elo import EloRatings, HOME_ADVANTAGE
from app.services import calibration_layer

SPORT = "international"
FALLBACK_TOTAL = 2.55          # typical WC scoring rate; replaced by observed
SHRINK_N = 8                   # team samples get weight n/(n+SHRINK_N)


def _pois(k: int, lam: float) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


async def _tournament_baseline(log_table: str = "wc_match_log",
                               fallback: float = FALLBACK_TOTAL,
                               where: str = "", params: tuple = ()) -> float:
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            f"SELECT AVG(home_goals + away_goals), COUNT(*) FROM {log_table} {where}",
            params)).fetchone()
    if row and row[1] and row[1] >= 20:
        return float(row[0])
    return fallback


async def _team_gf_ga(team: str, log_table: str = "wc_match_log",
                      recent_n: int = 0) -> tuple[float, float, int]:
    """Team's goals for/against per game in the given log (0,0,0 if unseen).
    recent_n > 0 restricts to the team's most recent N matches (club form)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        q = (f"SELECT home,away,home_goals,away_goals FROM {log_table} "
             f"WHERE home=? OR away=? ORDER BY date DESC")
        rows = await (await db.execute(q, (team, team))).fetchall()
    if recent_n:
        rows = rows[:recent_n]
    gf = ga = 0
    for r in rows:
        if r["home"] == team: gf += r["home_goals"]; ga += r["away_goals"]
        else: gf += r["away_goals"]; ga += r["home_goals"]
    n = len(rows)
    return (gf / n if n else 0.0, ga / n if n else 0.0, n)


def _solve_lambdas(total: float, p_home_2way: float) -> tuple[float, float]:
    """Find (lh, la) with lh+la=total whose Poisson win-prob ratio matches
    the Elo two-way expectation. Simple monotonic grid search."""
    best, best_err = (total / 2, total / 2), 9.9
    for i in range(1, 40):
        lh = total * i / 40
        la = total - lh
        pw = pl = 0.0
        for h in range(9):
            ph = _pois(h, lh)
            for a in range(9):
                pa = _pois(a, la)
                if h > a: pw += ph * pa
                elif h < a: pl += ph * pa
        two = pw / (pw + pl) if (pw + pl) > 0 else 0.5
        err = abs(two - p_home_2way)
        if err < best_err:
            best_err, best = err, (lh, la)
    return best


# Truncating the score grid discards only high-scoring outcomes, so the lost
# mass is renormalised back across every scoreline — which biases the totals
# lines downward by roughly the size of that tail. The worst case is a lopsided
# fixture (lambda ~3.2 vs ~0.4), not an even one, because the fatter side's tail
# dominates. Measured worst-case discarded mass across the clamped lambda range:
#   10 goals -> 2.8e-03   12 -> 2.3e-04   15 -> 3.3e-06
# 15 costs 225 cells instead of 100 and puts the error two orders of magnitude
# below the 1e-4 we round to.
MAX_GOALS = 15
TOTALS_LINES = (0.5, 1.5, 2.5, 3.5, 4.5)


def _score_matrix(lh: float, la: float, max_goals: int = MAX_GOALS) -> list[list[float]]:
    """Joint P(home=h, away=a), normalised over the truncated grid."""
    rows = [[_pois(h, lh) * _pois(a, la) for a in range(max_goals)]
            for h in range(max_goals)]
    s = sum(p for row in rows for p in row)
    return [[p / s for p in row] for row in rows] if s else rows


def derive_markets(matrix: list[list[float]], top_scores: int = 5) -> dict:
    """Totals, both-teams-to-score, and likeliest scorelines from the same
    score matrix the 1X2 probabilities come from.

    These are free — the matrix is already computed — but they are NOT passed
    through the calibration layer. That alpha is fitted on 1X2 outcomes only;
    applying it to a totals market would be borrowing a correction from a
    different question. Reported raw, and labelled as such.
    """
    totals, btts_yes = {}, 0.0
    for h, row in enumerate(matrix):
        for a, p in enumerate(row):
            if h > 0 and a > 0:
                btts_yes += p
    for line in TOTALS_LINES:
        over = sum(p for h, row in enumerate(matrix)
                   for a, p in enumerate(row) if h + a > line)
        totals[f"{line}"] = {"over": round(over, 4), "under": round(1 - over, 4)}

    flat = sorted(((p, h, a) for h, row in enumerate(matrix)
                   for a, p in enumerate(row)), reverse=True)
    return {
        "totals": totals,
        "btts": {"yes": round(btts_yes, 4), "no": round(1 - btts_yes, 4)},
        "correct_score": [{"score": f"{h}-{a}", "p": round(p, 4)}
                          for p, h, a in flat[:top_scores]],
        "calibrated": False,
        "note": ("Derived from the Dixon-Coles score matrix. Only the 1X2 "
                 "probabilities are calibrated; these are raw model output."),
    }


async def predict_v2(home: str, away: str, neutral: bool = True,
                     sport: str = SPORT, log_table: str = "wc_match_log",
                     fallback_total: float = FALLBACK_TOTAL,
                     recent_n: int = 0) -> dict:
    """Draw-aware H/D/A with calibrated probabilities and expected goals.
    Generic over sport + results log (internationals by default; clubs pass
    their own Elo namespace, club_match_log/historical table, and baseline)."""
    elo = EloRatings(sport)
    r = await elo.get_many([home, away])
    diff = r[home] - r[away] + (0 if neutral else HOME_ADVANTAGE)
    p2 = 1.0 / (1.0 + 10 ** (-diff / 400.0))

    # Total-goals estimate: competition baseline nudged by both teams' records
    base = await _tournament_baseline(log_table, fallback_total)
    gf_h, ga_h, n_h = await _team_gf_ga(home, log_table, recent_n)
    gf_a, ga_a, n_a = await _team_gf_ga(away, log_table, recent_n)
    w_h = n_h / (n_h + SHRINK_N)
    w_a = n_a / (n_a + SHRINK_N)
    team_signal = ((gf_h + ga_h) * w_h + (gf_a + ga_a) * w_a) / max(w_h + w_a, 1e-9) \
        if (w_h + w_a) > 0 else base
    total = max(1.3, min(3.6, (1 - 0.35) * base + 0.35 * team_signal))

    lh, la = _solve_lambdas(total, p2)

    matrix = _score_matrix(lh, la)
    pH = pD = pA = 0.0
    for h, row in enumerate(matrix):
        for a, p in enumerate(row):
            if h > a: pH += p
            elif h == a: pD += p
            else: pA += p
    s = pH + pD + pA
    raw = [pH / s, pD / s, pA / s]

    # Per-sport alpha: club and international models miscalibrate in opposite
    # directions, so a shared factor would push one of them the wrong way.
    cal = calibration_layer.apply(raw, sport=sport)
    labels = [home, "Draw", away]
    pick = labels[max(range(3), key=lambda i: cal[i])]
    return {
        "p_home": round(cal[0], 4), "p_draw": round(cal[1], 4), "p_away": round(cal[2], 4),
        "raw": [round(x, 4) for x in raw],
        "xg_home": round(lh, 2), "xg_away": round(la, 2), "xg_total": round(total, 2),
        "pick": pick,
        "markets": derive_markets(matrix),
        "model": "v2-poisson-calibrated",
    }
