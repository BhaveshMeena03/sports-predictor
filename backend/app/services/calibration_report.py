"""Reliability curves and Brier decomposition — the calibration evidence.

Accuracy is the wrong headline for this model: it never picks a draw (no 1X2
model does, since a draw is rarely the argmax), so ~27% of matches are
unwinnable on that metric and the number says little about probability quality.
What actually matters is whether a stated 60% happens about 60% of the time.
That is what these functions measure, and it is the only claim worth selling.

Brier decomposition (Murphy 1973):

    Brier = reliability - resolution + uncertainty

  reliability  lower is better — how far stated probabilities sit from observed
               frequencies. This is calibration proper.
  resolution   higher is better — how much predictions vary from the base rate.
               A model that always says "1/3 each" is perfectly reliable and
               completely useless; resolution is what separates the two.
  uncertainty  a property of the outcomes, not the model. Identical for every
               model scored on the same matches, so it is the fair baseline to
               compare against.
"""

from collections import defaultdict

# Wider bins than the usual 10: a single league-season is ~380 matches, and
# 10 bins over 3 outcomes leaves buckets too thin to read anything from.
DEFAULT_BINS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.65, 0.8, 1.01)

OUTCOME_LABELS = ("home", "draw", "away")


def _bin_index(p: float, bins=DEFAULT_BINS) -> int:
    for i in range(len(bins) - 1):
        if bins[i] <= p < bins[i + 1]:
            return i
    return len(bins) - 2


def reliability_curve(samples: list[dict], key: str = "probs",
                      bins=DEFAULT_BINS) -> list[dict]:
    """Bin every (stated probability, did-it-happen) pair.

    Each match contributes three points — one per outcome — because the claim
    being tested is about the probability vector, not just the pick.
    """
    buckets: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for s in samples:
        probs = s.get(key)
        if not probs:
            continue
        for i, p in enumerate(probs):
            buckets[_bin_index(p, bins)].append((p, 1 if s["outcome"] == i else 0))

    out = []
    for b in sorted(buckets):
        pairs = buckets[b]
        n = len(pairs)
        mean_p = sum(p for p, _ in pairs) / n
        observed = sum(hit for _, hit in pairs) / n
        out.append({
            "bin": f"{bins[b]:.0%}-{min(bins[b + 1], 1.0):.0%}",
            "n": n,
            "predicted": round(mean_p, 4),
            "observed": round(observed, 4),
            # Positive => model claimed more than happened (overconfident).
            "gap": round(mean_p - observed, 4),
        })
    return out


def brier_decomposition(samples: list[dict], key: str = "probs",
                        bins=DEFAULT_BINS) -> dict:
    """Murphy decomposition of the mean Brier score."""
    points: list[tuple[float, int]] = []
    for s in samples:
        probs = s.get(key)
        if not probs:
            continue
        for i, p in enumerate(probs):
            points.append((p, 1 if s["outcome"] == i else 0))
    if not points:
        return {}

    n = len(points)
    base_rate = sum(o for _, o in points) / n
    brier = sum((p - o) ** 2 for p, o in points) / n

    buckets: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for p, o in points:
        buckets[_bin_index(p, bins)].append((p, o))

    reliability = resolution = 0.0
    for pairs in buckets.values():
        nk = len(pairs)
        mean_p = sum(p for p, _ in pairs) / nk
        obs = sum(o for _, o in pairs) / nk
        reliability += nk * (mean_p - obs) ** 2
        resolution += nk * (obs - base_rate) ** 2
    reliability /= n
    resolution /= n
    uncertainty = base_rate * (1 - base_rate)

    return {
        "brier": round(brier, 5),
        "reliability": round(reliability, 5),
        "resolution": round(resolution, 5),
        "uncertainty": round(uncertainty, 5),
        # Brier - (REL - RES + UNC). Not exactly zero by design: the identity
        # holds only if probabilities are constant within each bin, so this
        # residual is the within-bin variance. Expect a small negative number
        # (<1% of Brier); anything larger means the bins are too wide to trust.
        "identity_check": round(brier - (reliability - resolution + uncertainty), 6),
        "n_points": n,
    }


def fit_shrinkage(samples: list[dict], key: str = "probs") -> dict:
    """Grid-search the shrink-toward-uniform factor that minimises Brier.

    Same one-parameter form as calibration_layer.apply(), fitted here on club
    data. alpha < 1 means overconfident. One parameter over hundreds of matches
    won't overfit — anything richer (isotonic, per-bin) would, on this much data.
    """
    def brier_at(alpha: float) -> float:
        tot = 0.0
        cnt = 0
        for s in samples:
            probs = s.get(key)
            if not probs:
                continue
            cal = [alpha * p + (1 - alpha) / 3 for p in probs]
            tot += sum((cal[i] - (1.0 if s["outcome"] == i else 0.0)) ** 2
                       for i in range(3)) / 3
            cnt += 1
        return tot / cnt if cnt else float("inf")

    # Ties break toward 1.0 (no adjustment) — see calibration_layer.fit_alpha:
    # a model already predicting uniform is invariant under shrink-toward-uniform,
    # so every alpha scores the same and a plain min() would invent a correction.
    grid = [round(0.40 + 0.02 * i, 2) for i in range(46)]   # 0.40 .. 1.30
    best = min(grid, key=lambda a: (round(brier_at(a), 9), abs(a - 1.0)))
    raw, cal = brier_at(1.0), brier_at(best)
    return {
        "alpha": best,
        "brier_raw": round(raw, 5),
        "brier_calibrated": round(cal, 5),
        "improvement": round(raw - cal, 5),
        "read": ("overconfident — shrink helps" if best < 0.98 else
                 "underconfident — sharpen helps" if best > 1.02 else
                 "already well calibrated"),
    }


async def fit_and_store(league_key: str, season: str = "2526") -> dict:
    """Fit this league's alpha from the walk-forward backtest and persist it.

    Club leagues have no live prediction log until the season starts, so the
    backtest is the only honest source. It is genuinely out-of-sample — each
    match is predicted from ratings that exclude it, then the ratings update —
    but it is a backtest, so the stored `basis` says so.
    """
    from app.services import calibration_layer
    from app.services.club_service import LEAGUES, backtest_league_season

    sport = LEAGUES[league_key]["sport"]
    bt = await backtest_league_season(league_key, season=season, collect=True)
    samples = bt.get("samples", [])
    if len(samples) < 30:
        return {"league": league_key, "sport": sport, "n": len(samples),
                "note": "too few matches to fit; leaving alpha unset (=1.0)"}

    # Fit against the PRE-calibration vector, never the served one — otherwise
    # each refit composes with the alpha already in force and the value drifts.
    fit = fit_shrinkage(samples, key="probs_raw")
    await calibration_layer.store(
        sport, fit["alpha"], len(samples),
        fit["brier_raw"], fit["brier_calibrated"])
    return {"league": bt["league"], "sport": sport, "season": season,
            "basis": "walk_forward_backtest", "n": len(samples), **fit}


async def fit_all_clubs(season: str = "2526") -> dict:
    from app.services.club_service import LEAGUES
    return {lg: await fit_and_store(lg, season) for lg in LEAGUES}


async def league_report(league_key: str, season: str = "2526") -> dict:
    """Full calibration evidence for one league-season, model vs market."""
    from app.services.club_service import backtest_league_season

    bt = await backtest_league_season(league_key, season=season, collect=True)
    samples = bt.get("samples", [])
    with_market = [s for s in samples if s.get("market")]

    report = {
        "league": bt["league"],
        "season": season,
        "matches": len(samples),
        # This is a walk-forward backtest (predict, then update), NOT a live
        # log. Kept explicit so the distinction can't quietly blur.
        "basis": "walk_forward_backtest",
        "model": {
            "decomposition": brier_decomposition(samples),
            "reliability_curve": reliability_curve(samples),
            "shrinkage_fit": fit_shrinkage(samples),
        },
    }
    if with_market:
        report["market"] = {
            "matches": len(with_market),
            "decomposition": brier_decomposition(with_market, key="market"),
            "reliability_curve": reliability_curve(with_market, key="market"),
        }
        model_on_same = brier_decomposition(with_market)
        report["vs_market"] = {
            "model_brier": model_on_same["brier"],
            "market_brier": report["market"]["decomposition"]["brier"],
            "gap": round(model_on_same["brier"]
                         - report["market"]["decomposition"]["brier"], 5),
            "note": ("Positive gap = market is sharper. The closing line is the "
                     "hardest public benchmark there is; being close to it is "
                     "the claim, beating it is not."),
        }
    return report
