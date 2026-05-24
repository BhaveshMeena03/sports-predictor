"""
Market-odds utilities.

The betting market (especially Pinnacle's closing line) is the most accurate
public predictor that exists. We use it two ways:
  1. As a strong input to our ensemble (a prior we should rarely stray far from)
  2. As the benchmark for VALUE detection — we only bet where our estimate beats
     the market's price by enough margin to overcome the vig.

De-vigging: raw implied probs (1/odds) sum to >1 because of the bookmaker margin
(the "overround" or "vig"). We normalise them back to sum=1 to get the market's
true probability estimate.
"""


def implied_probs(odds_home: float, odds_draw: float, odds_away: float) -> dict | None:
    """De-vig 1X2 decimal odds → true probability estimate (sums to 1.0)."""
    if not (odds_home and odds_draw and odds_away):
        return None
    if odds_home <= 1 or odds_draw <= 1 or odds_away <= 1:
        return None
    raw = [1.0 / odds_home, 1.0 / odds_draw, 1.0 / odds_away]
    overround = sum(raw)  # > 1.0; the bookmaker's margin
    probs = [r / overround for r in raw]
    return {
        "p_home": round(probs[0], 4),
        "p_draw": round(probs[1], 4),
        "p_away": round(probs[2], 4),
        "overround": round(overround, 4),
        "vig_pct": round((overround - 1) * 100, 2),
    }


def expected_value(model_prob: float, decimal_odds: float) -> float:
    """EV per £1 staked. Positive = +EV bet.
    EV = p * (odds - 1) - (1 - p)  =  p * odds - 1
    """
    return model_prob * decimal_odds - 1.0


def find_value(
    model_probs: list[float],
    odds: list[float],
    edge_threshold: float = 0.0,
) -> list[dict]:
    """Compare model probabilities to market odds across the 3 outcomes.

    Returns the outcomes where EV > edge_threshold, sorted by EV descending.
    edge_threshold is in EV-per-£1 terms (e.g. 0.05 = require +5% EV).
    """
    labels = ["home", "draw", "away"]
    out = []
    for i in range(3):
        if i >= len(odds) or not odds[i] or odds[i] <= 1:
            continue
        ev = expected_value(model_probs[i], odds[i])
        market_p = 1.0 / odds[i]
        if ev > edge_threshold:
            out.append({
                "outcome": labels[i],
                "model_prob": round(model_probs[i], 4),
                "market_implied_prob": round(market_p, 4),
                "decimal_odds": odds[i],
                "edge": round(model_probs[i] - market_p, 4),
                "expected_value": round(ev, 4),
            })
    out.sort(key=lambda x: x["expected_value"], reverse=True)
    return out
