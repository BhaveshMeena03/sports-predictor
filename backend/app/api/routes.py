import logging

import aiosqlite
from fastapi import APIRouter, HTTPException
from app.models.schemas import (
    MatchAnalysisRequest, MultiBetRequest, BetRecord,
    QuickAnalysisRequest, FixturesRequest
)
from app.core.config import settings
from app.core.database import DB_PATH
from app.services.ai_analyzer import ai_analyzer
from app.services.football_service import football_service, LEAGUE_IDS
from app.services.odds_service import odds_service
from app.services.nba_nhl_service import nba_service, nhl_service
from app.services.quota import api_football_quota, odds_api_quota
from app.services.elo import EloRatings
from app.services.backtest import run_backtest
from app.services.ensemble import blend, get_weights, set_weights, pick_from_blend, DEFAULT_WEIGHTS
from app.services.poisson import load_model as load_poisson_model

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "service": "Sports Predictor AI"}


@router.get("/quota")
async def quota():
    """How many external API calls we've used today/this month."""
    return {
        "api_football": api_football_quota.status(),
        "odds_api": odds_api_quota.status(),
    }


@router.get("/dashboard")
async def dashboard():
    """One-stop proof + health surface: model registry, live calibration, recent backtests."""
    from app.services.dashboard import build
    out = await build()
    out["quota"] = {"api_football": api_football_quota.status(),
                    "odds_api": odds_api_quota.status()}
    return out


@router.get("/scheduler/status")
async def scheduler_status():
    """Background scheduler state + last run summary."""
    from app.services.scheduler import status
    return status()


@router.post("/scheduler/run-now")
async def scheduler_run_now():
    """Manually trigger the daily model-refresh job (re-ingest intl + refit DC)."""
    from app.services.scheduler import refresh_models
    return await refresh_models()


# ─── Fixtures ───────────────────────────────────────────

@router.post("/fixtures")
async def get_fixtures(req: FixturesRequest):
    if req.sport == "football":
        league_id = LEAGUE_IDS.get(req.league, 39)
        fixtures = await football_service.get_fixtures(league_id, req.date)
        return {"sport": req.sport, "league": req.league, "fixtures": fixtures}

    elif req.sport == "nba":
        games = await nba_service.get_scoreboard(req.date)
        return {"sport": "nba", "games": games}

    elif req.sport == "nhl":
        games = await nhl_service.get_scoreboard(req.date)
        return {"sport": "nhl", "games": games}

    raise HTTPException(400, f"Unsupported sport: {req.sport}")


# ─── Match Analysis ─────────────────────────────────────

async def _ensure_prediction_prob_cols(db) -> None:
    """Lazily add probability columns to the predictions table (for Brier scoring)."""
    cur = await db.execute("PRAGMA table_info(predictions)")
    cols = {row[1] for row in await cur.fetchall()}
    for col in ("p_home", "p_draw", "p_away"):
        if col not in cols:
            await db.execute(f"ALTER TABLE predictions ADD COLUMN {col} REAL")
    await db.commit()


async def _log_prediction(req: MatchAnalysisRequest, analysis: dict, odds_used: float | None,
                          probs: list[float] | None = None) -> int | None:
    """Persist every analysis so we can score calibration once bets settle."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await _ensure_prediction_prob_cols(db)
            ph, pd, pa = (probs or [None, None, None])
            cur = await db.execute(
                """INSERT INTO predictions
                   (sport, league, home_team, away_team, match_date,
                    prediction, confidence, odds, reasoning, p_home, p_draw, p_away)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    req.sport, req.league, req.home_team, req.away_team, req.date,
                    str(analysis.get("recommendation", "")),
                    analysis.get("confidence"),
                    odds_used,
                    str(analysis.get("reasoning", "")),
                    ph, pd, pa,
                ),
            )
            await db.commit()
            return cur.lastrowid
    except Exception as e:
        log.warning("prediction log write failed: %s", e)
        return None


@router.post("/analyze")
async def analyze_match(req: MatchAnalysisRequest):
    match_data = {
        "sport": req.sport,
        "league": req.league,
        "home_team": req.home_team,
        "away_team": req.away_team,
        "date": req.date,
        "venue": req.venue or f"{req.home_team} Home",
        "is_home": True,
        "extra_context": req.extra_context,
    }

    # Auto-hydrate: standings, form, H2H, injuries — all in parallel
    if req.sport == "football":
        league_id = LEAGUE_IDS.get(req.league) if req.league else None
        try:
            context = await football_service.hydrate_match(
                req.home_team, req.away_team, league_id
            )
            match_data.update(context)
        except Exception as e:
            log.warning("hydrate_match failed: %s", e)

    # Fetch odds if available
    odds_used = None
    if req.league:
        try:
            odds = await odds_service.get_odds(req.league)
            for o in odds:
                if (req.home_team.lower() in o.get("home_team", "").lower() or
                    req.away_team.lower() in o.get("away_team", "").lower()):
                    match_data["odds"] = o.get("avg_odds")
                    # Track the odds we'd be betting at (home side)
                    avg = o.get("avg_odds") or {}
                    odds_used = avg.get(o.get("home_team")) or None
                    break
        except Exception as e:
            log.warning("odds fetch failed for league=%s: %s", req.league, e)

    analysis = await ai_analyzer.analyze_match(match_data)

    # ─── Ensemble: pull each base-model probability vector and blend ─────
    weights, shrinkage = await get_weights(req.sport)
    base_preds: dict[str, list[float]] = {}

    if req.sport == "football" and weights.get("elo", 0) > 0:
        elo = EloRatings("football")
        p = await elo.predict_1x2(req.home_team, req.away_team)
        base_preds["elo"] = [p["p_home"], p["p_draw"], p["p_away"]]
    elif req.sport in ("nba", "nhl") and weights.get("elo", 0) > 0:
        elo = EloRatings(req.sport)
        p = await elo.predict_1x2(req.home_team, req.away_team)
        base_preds["elo"] = [p["p_home"], p["p_draw"], p["p_away"]]
    elif req.sport == "international" and weights.get("elo", 0) > 0:
        # Neutral-venue national-team Elo (most WC games are neutral)
        from app.services.worldcup import predict as wc_predict
        neutral = req.venue is None or "neutral" in (req.venue or "").lower()
        p = await wc_predict(req.home_team, req.away_team, neutral=neutral)
        base_preds["elo"] = [p["p_home"], p["p_draw"], p["p_away"]]

    if req.sport == "football" and weights.get("poisson", 0) > 0 and req.league:
        dc = await load_poisson_model("football", req.league, settings.API_FOOTBALL_SEASON)
        if dc:
            p = dc.predict_1x2(req.home_team, req.away_team)
            if p:
                base_preds["poisson"] = [p["p_home"], p["p_draw"], p["p_away"]]

    if weights.get("llm", 0) > 0 and isinstance(analysis, dict) and "implied_probability" in analysis:
        # Map LLM's chosen recommendation onto a 3-prob vector
        p_pick = float(analysis["implied_probability"])
        rec = (analysis.get("recommendation") or "").lower()
        rest = (1 - p_pick) / 2
        if "home" in rec or req.home_team.lower() in rec:
            base_preds["llm"] = [p_pick, rest, rest]
        elif "away" in rec or req.away_team.lower() in rec:
            base_preds["llm"] = [rest, rest, p_pick]
        elif "draw" in rec:
            base_preds["llm"] = [rest, p_pick, rest]
        # else: skip LLM contribution

    ensemble_probs = blend(base_preds, weights, shrinkage=shrinkage) if base_preds else None
    ensemble_pick = pick_from_blend(ensemble_probs, req.home_team, req.away_team) if ensemble_probs else None

    # Persist for calibration tracking — use ENSEMBLE recommendation/confidence
    # (that's the actual production output now), not LLM's.
    prediction_id = None
    if isinstance(analysis, dict) and "error" not in analysis:
        log_payload = dict(analysis)
        if ensemble_pick:
            log_payload["recommendation"] = ensemble_pick["recommendation"]
            log_payload["confidence"] = ensemble_pick["confidence"]
        prediction_id = await _log_prediction(req, log_payload, odds_used, probs=ensemble_probs)

    return {
        "match": f"{req.home_team} vs {req.away_team}",
        "prediction_id": prediction_id,
        "ensemble": {
            "recommendation": ensemble_pick["recommendation"] if ensemble_pick else None,
            "confidence": ensemble_pick["confidence"] if ensemble_pick else None,
            "p_home": ensemble_probs[0] if ensemble_probs else None,
            "p_draw": ensemble_probs[1] if ensemble_probs else None,
            "p_away": ensemble_probs[2] if ensemble_probs else None,
            "weights_used": weights,
            "shrinkage": shrinkage,
            "models_contributed": list(base_preds.keys()),
        },
        "base_models": base_preds,
        "llm_analysis": analysis,
    }


# ─── Quick Analysis (Natural Language) ──────────────────

@router.post("/quick-analyze")
async def quick_analyze(req: QuickAnalysisRequest):
    match_data = {
        "sport": "multi",
        "home_team": "",
        "away_team": "",
        "extra_context": req.query,
    }
    analysis = await ai_analyzer.analyze_match(match_data)
    return {"query": req.query, "analysis": analysis}


# ─── Multi-Bet Analysis ────────────────────────────────

async def _calibration_multiplier(confidence: float) -> float:
    """Return 0..1 multiplier based on the bucket's historical hit rate.

    If you claim 70% confidence and actually win 60% of those, we scale
    your stake down by 60/70 ≈ 0.86. If you claim 70% and win 80%, we scale
    UP toward 1.0 (capped — never bigger than 1.0). Requires settled bets.
    """
    if confidence is None or confidence <= 0:
        return 1.0
    if confidence < 50:
        bucket = "<50"; band = (0, 50)
    elif confidence < 70:
        bucket = "50-69"; band = (50, 70)
    elif confidence < 85:
        bucket = "70-84"; band = (70, 85)
    else:
        bucket = "85+"; band = (85, 100)
    band_mid = sum(band) / 2

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("""
                SELECT p.confidence, b.result
                FROM predictions p
                JOIN bets b ON b.match = (p.home_team || ' vs ' || p.away_team)
                WHERE b.result IN ('won', 'lost') AND p.confidence IS NOT NULL
            """)).fetchall()
    except Exception:
        return 1.0

    bucket_rows = [r for r in rows
                   if (band[0] <= (r["confidence"] or 0) < band[1] or
                       (band[1] == 100 and r["confidence"] == 100))]
    if len(bucket_rows) < 5:
        # Not enough data to trust — no adjustment
        return 1.0
    won = sum(1 for r in bucket_rows if r["result"] == "won")
    actual_pct = won / len(bucket_rows) * 100
    # Multiplier = actual / claimed band midpoint, capped at 1.0
    return min(actual_pct / band_mid, 1.0)


async def _kelly_stake(win_prob: float, decimal_odds: float, bankroll: float,
                       fraction: float = 0.25, confidence: float = None) -> dict:
    """Fractional Kelly criterion, optionally scaled by historical calibration.
    f* = (bp - q) / b   where b = odds-1, p = win prob, q = 1-p.
    Quarter-Kelly by default. If `confidence` is provided AND we have ≥5 settled
    bets in that confidence bucket, scale by the bucket's actual-vs-claimed ratio.
    """
    if win_prob <= 0 or decimal_odds <= 1 or bankroll <= 0:
        return {"recommended_stake": 0, "kelly_fraction": 0, "note": "No edge / no bankroll"}
    b = decimal_odds - 1
    p = win_prob
    q = 1 - p
    full_kelly = (b * p - q) / b
    if full_kelly <= 0:
        return {
            "recommended_stake": 0,
            "kelly_fraction": round(full_kelly, 4),
            "note": "Negative EV — Kelly says don't bet",
        }
    fractional = full_kelly * fraction
    cal_mult = await _calibration_multiplier(confidence) if confidence is not None else 1.0
    final = fractional * cal_mult
    return {
        "recommended_stake": round(bankroll * final, 2),
        "kelly_fraction": round(final, 4),
        "full_kelly_fraction": round(full_kelly, 4),
        "calibration_multiplier": round(cal_mult, 3),
        "note": f"Quarter-Kelly × calibration {round(cal_mult,2)}: "
                f"stake {round(final*100,2)}% of £{bankroll} bankroll",
    }


@router.post("/analyze-multi")
async def analyze_multi_bet(req: MultiBetRequest):
    legs_data = [leg.model_dump() for leg in req.legs]

    # Calculate basic stats
    total_odds = 1.0
    for leg in req.legs:
        total_odds *= leg.odds

    analysis = await ai_analyzer.analyze_multi(legs_data)

    response = {
        "legs": len(req.legs),
        "total_odds": round(total_odds, 2),
        "stake": req.stake,
        "potential_payout": round((req.stake or 0) * total_odds, 2),
        "analysis": analysis,
    }

    # Kelly-criterion stake sizing if bankroll provided + analysis succeeded
    if req.bankroll and isinstance(analysis, dict) and "combined_probability" in analysis:
        cp = analysis["combined_probability"]
        response["kelly"] = await _kelly_stake(
            win_prob=cp,
            decimal_odds=total_odds,
            bankroll=req.bankroll,
            confidence=cp * 100,
        )

    return response


# ─── Odds ───────────────────────────────────────────────

@router.get("/odds/{sport}")
async def get_odds(sport: str):
    odds = await odds_service.get_odds(sport)
    if not odds:
        return {"sport": sport, "message": "No odds available. Set ODDS_API_KEY in .env", "odds": []}
    return {"sport": sport, "odds": odds}

@router.get("/upcoming-with-odds")
async def get_upcoming_with_odds(sports: str = None):
    """Get all upcoming matches with odds across all major sports for next 7 days."""
    import asyncio

    all_sport_configs = [
        ("premier_league", "EPL"),
        ("la_liga", "La Liga"),
        ("bundesliga", "Bundesliga"),
        ("serie_a", "Serie A"),
        ("ligue_1", "Ligue 1"),
        ("champions_league", "Champions League"),
        ("mls", "MLS"),
        ("nba", "NBA"),
        ("nhl", "NHL"),
        ("ipl", "IPL"),
    ]

    # Filter to requested sports only (saves API calls)
    if sports:
        requested = [s.strip() for s in sports.split(",")]
        sport_configs = [(k, l) for k, l in all_sport_configs if k in requested]
    else:
        sport_configs = all_sport_configs

    async def fetch_sport(sport_key: str, sport_label: str):
        matches = []
        try:
            odds = await odds_service.get_odds(sport_key)
            for event in odds:
                avg = event.get("avg_odds", {})
                home_odds = avg.get(event.get("home_team", ""), 0)
                away_odds = avg.get(event.get("away_team", ""), 0)
                draw_odds = avg.get("Draw", 0)

                matches.append({
                    "id": event.get("id"),
                    "sport": sport_key,
                    "sport_label": sport_label,
                    "home_team": event.get("home_team", ""),
                    "away_team": event.get("away_team", ""),
                    "commence_time": event.get("commence_time", ""),
                    "home_odds": round(home_odds, 2),
                    "away_odds": round(away_odds, 2),
                    "draw_odds": round(draw_odds, 2) if draw_odds else None,
                })
        except Exception as e:
            print(f"Error fetching {sport_key}: {e}")
        return matches

    # Fetch all sports in parallel
    results = await asyncio.gather(
        *[fetch_sport(key, label) for key, label in sport_configs],
        return_exceptions=True
    )

    all_matches = []
    for r in results:
        if isinstance(r, list):
            all_matches.extend(r)

    all_matches.sort(key=lambda x: x.get("commence_time", ""))
    return {"matches": all_matches, "total": len(all_matches)}


# ─── Best Bets (next 7 days, ranked by safety/value) ───

@router.get("/best-bets")
async def best_bets(
    sports: str = None,
    max_odds: float = 1.60,
    min_odds: float = 1.18,
    limit: int = 10,
):
    """Find the safest favourites across all sports for the next 7 days.

    Ranks by implied probability (1/odds). No AI calls — pure odds-driven shortlist
    so it's fast and free. Use `/api/analyze` on any pick for deep analysis."""
    from datetime import datetime, timedelta, timezone

    upcoming = await get_upcoming_with_odds(sports)
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=7)

    candidates = []
    for m in upcoming["matches"]:
        try:
            t = datetime.fromisoformat(m["commence_time"].replace("Z", "+00:00"))
        except Exception:
            continue
        if not (now <= t <= cutoff):
            continue
        # Identify the favourite side
        ho, ao = m.get("home_odds") or 0, m.get("away_odds") or 0
        if ho <= 0 or ao <= 0:
            continue
        if ho <= ao:
            side, team, odds = "HOME", m["home_team"], ho
        else:
            side, team, odds = "AWAY", m["away_team"], ao
        if not (min_odds <= odds <= max_odds):
            continue
        candidates.append({
            "match": f"{m['home_team']} vs {m['away_team']}",
            "pick": f"{team} Win",
            "side": side,
            "odds": odds,
            "implied_probability": round(1 / odds, 3),
            "sport": m["sport_label"],
            "sport_key": m["sport"],
            "commence_time": m["commence_time"],
            "event_id": m.get("id"),
        })

    candidates.sort(key=lambda x: x["odds"])  # shortest (safest) first
    return {
        "filter": {"min_odds": min_odds, "max_odds": max_odds, "next_days": 7},
        "count": len(candidates),
        "bets": candidates[:limit],
    }


# ─── Elo ratings ───────────────────────────────────────

@router.get("/elo/{sport}")
async def get_elo(sport: str, limit: int = 50):
    """Top teams by Elo rating for the given sport."""
    elo = EloRatings(sport)
    return {"sport": sport, "ratings": await elo.all_ratings(limit=limit)}


@router.get("/elo/{sport}/predict")
async def predict_elo(sport: str, home: str, away: str):
    """Quick Elo-only 1X2 prediction (no LLM, no API calls)."""
    elo = EloRatings(sport)
    return {"sport": sport, "home_team": home, "away_team": away,
            "prediction": await elo.predict_1x2(home, away)}


# ─── Ensemble weights ─────────────────────────────────

@router.get("/ensemble/weights/{sport}")
async def get_ensemble_weights(sport: str):
    weights, shrinkage = await get_weights(sport)
    return {"sport": sport, "weights": weights, "shrinkage": shrinkage,
            "defaults": DEFAULT_WEIGHTS.get(sport, {})}


@router.put("/ensemble/weights/{sport}")
async def update_ensemble_weights(sport: str, weights_json: str, shrinkage: float = 0.0):
    """Update per-sport ensemble weights. Pass weights_json as a JSON string,
    e.g. '{"elo": 0.5, "poisson": 0.4, "llm": 0.1}'. Components don't need to
    sum to 1.0 — blend() renormalises."""
    import json as _json
    try:
        w = _json.loads(weights_json)
    except Exception:
        raise HTTPException(400, "weights_json must be valid JSON")
    await set_weights(sport, w, shrinkage)
    return {"sport": sport, "weights": w, "shrinkage": shrinkage, "message": "saved"}


# ─── Dixon-Coles fit (one-shot for a league) ───────────

@router.post("/poisson/fit/{league_key}")
async def fit_poisson(league_key: str, season: int = None):
    """Fit Dixon-Coles on the requested league's full season and save the model.

    Run this once per season per league. The /analyze endpoint will then pick up
    the saved model. ~5-10 seconds per league."""
    from app.services.poisson import DixonColes, save_model
    from app.services.backtest import fetch_finished_fixtures
    season = season or settings.API_FOOTBALL_SEASON

    try:
        fixtures = await fetch_finished_fixtures(league_key, season=season, limit=500)
    except ValueError as e:
        raise HTTPException(400, str(e))

    matches = []
    teams = set()
    for fx in fixtures:
        h = fx["teams"]["home"]["name"]; a = fx["teams"]["away"]["name"]
        hg = fx["goals"]["home"]; ag = fx["goals"]["away"]
        if hg is None or ag is None:
            continue
        matches.append({"home": h, "away": a, "home_goals": hg, "away_goals": ag})
        teams.add(h); teams.add(a)

    if len(matches) < 50:
        raise HTTPException(400, f"Only {len(matches)} matches found; need at least 50 to fit")

    model = DixonColes(sorted(teams))
    info = model.fit(matches, verbose=True)
    await save_model("football", league_key, season, model, len(matches))
    info["league"] = league_key
    info["season"] = season
    info["teams"] = len(teams)
    return info


# ─── Backtest ──────────────────────────────────────────

@router.post("/historical/download")
async def historical_download(leagues: str = None, seasons: str = None):
    """Download football-data.co.uk CSVs (results + closing odds) into SQLite.

    leagues : comma-list (default: all big-5).  seasons: comma-list like '2425,2324,2223'.
    Free, no quota impact. Run once, then use /historical/backtest."""
    from app.services.historical_data import download_all
    lg = [x.strip() for x in leagues.split(",")] if leagues else None
    sn = [x.strip() for x in seasons.split(",")] if seasons else None
    return await download_all(leagues=lg, seasons=sn)


@router.post("/historical/backtest")
async def historical_backtest(
    league: str = "premier_league",
    seasons: str = None,
    score_fraction: float = 0.3,
    value_threshold: float = 0.02,
):
    """Rigorous backtest on historical data WITH ROI (uses real closing odds).

    Compares naive/elo/poisson/market/ensembles, reporting Brier, log-loss,
    flat ROI, and value-bet ROI (only betting where we beat the closing line)."""
    from app.services.backtest_historical import run_historical_backtest
    sn = [x.strip() for x in seasons.split(",")] if seasons else None
    return await run_historical_backtest(
        league=league, seasons=sn,
        score_fraction=score_fraction, value_threshold=value_threshold,
    )


@router.post("/backtest")
async def backtest(
    leagues: str = "premier_league",
    pipelines: str = "elo,naive",
    matches_per_league: int = 200,
    score_fraction: float = 0.3,
    score_cap: int = None,
):
    """Replay historical matches through chosen pipelines.

    Query params:
      leagues             : comma-list (e.g. 'premier_league,la_liga')
      pipelines           : comma-list of {naive, elo, llm}.  'llm' costs Anthropic credits.
      matches_per_league  : total fixtures pulled per league (default 200 ≈ full season)
      score_fraction      : last X% scored; rest seed Elo (default 0.3 → 70% warmup)
      score_cap           : optional max scored matches per league (use to limit LLM cost)
    """
    league_list = [l.strip() for l in leagues.split(",") if l.strip()]
    pipeline_list = [p.strip() for p in pipelines.split(",") if p.strip()]
    return await run_backtest(
        leagues=league_list,
        pipelines=pipeline_list,
        matches_per_league=matches_per_league,
        score_fraction=score_fraction,
        score_cap=score_cap,
    )


# ─── World Cup ─────────────────────────────────────────

@router.post("/worldcup/bootstrap")
async def worldcup_bootstrap(overwrite: bool = False):
    """Seed Elo ratings for the 48 World Cup national teams.

    Run once before the tournament. Won't clobber ratings learned from real
    matches unless overwrite=true."""
    from app.services.worldcup import bootstrap_ratings
    return await bootstrap_ratings(overwrite=overwrite)


@router.get("/worldcup/predict")
async def worldcup_predict(home: str, away: str, neutral: bool = True):
    """Predict an international match using national-team Elo.

    neutral=true (default) strips home advantage (most WC games are neutral-venue).
    Blends with the LLM if you also want narrative context — see /analyze."""
    from app.services.worldcup import predict
    pred = await predict(home, away, neutral=neutral)
    labels = [f"{home} Win", "Draw", f"{away} Win"]
    probs = [pred["p_home"], pred["p_draw"], pred["p_away"]]
    best = max(range(3), key=lambda i: probs[i])
    return {
        "match": f"{home} vs {away}",
        "neutral_venue": neutral,
        "recommendation": labels[best],
        "confidence": round(probs[best] * 100, 1),
        "prediction": pred,
    }


@router.post("/worldcup/ingest")
async def worldcup_ingest(reset_to_seeds: bool = True):
    """Pull real international results (WC2022, Euro2024, Nations League, Copa,
    friendlies) from API-Football and update national-team Elo chronologically.
    This replaces hand-guessed seeds with data-driven ratings."""
    from app.services.worldcup import ingest_real_results
    return await ingest_real_results(reset_to_seeds=reset_to_seeds)


@router.get("/worldcup/ratings")
async def worldcup_ratings(limit: int = 48):
    """Current national-team Elo ladder."""
    elo = EloRatings("international")
    return {"sport": "international", "ratings": await elo.all_ratings(limit=limit)}


@router.post("/worldcup/simulate")
async def worldcup_simulate(n_sims: int = 10000, seed: int = None, groups_json: str = None):
    """Monte Carlo the whole tournament → advance / reach-final / win-the-cup odds.

    groups_json: optional JSON of {"A": ["Team1", ...4], ...}. If omitted, the
    top-48 rated teams are auto snake-seeded into 12 groups. Provide the REAL draw
    once it's known for accurate outright probabilities."""
    import json as _json
    from app.services.simulator import simulate
    groups = None
    if groups_json:
        try:
            groups = _json.loads(groups_json)
        except Exception:
            raise HTTPException(400, "groups_json must be valid JSON")
    return await simulate(groups=groups, n_sims=n_sims, seed=seed)


# ─── Calibration (predictions vs settled bets) ─────────

@router.get("/calibration")
async def calibration():
    """How well-calibrated are the AI's confidence scores?

    Joins logged predictions to settled bets (by match name) and bins by confidence.
    After ~20+ settled bets the buckets become meaningful."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("""
            SELECT p.confidence, b.result
            FROM predictions p
            JOIN bets b ON b.match = (p.home_team || ' vs ' || p.away_team)
            WHERE b.result IN ('won', 'lost') AND p.confidence IS NOT NULL
        """)).fetchall()

    buckets = {"<50": [], "50-69": [], "70-84": [], "85+": []}
    for r in rows:
        c = r["confidence"]
        won = 1 if r["result"] == "won" else 0
        if c < 50:
            buckets["<50"].append(won)
        elif c < 70:
            buckets["50-69"].append(won)
        elif c < 85:
            buckets["70-84"].append(won)
        else:
            buckets["85+"].append(won)

    return {
        "total_graded_bets": len(rows),
        "buckets": {
            label: {
                "n": len(vals),
                "actual_win_rate": round(sum(vals) / len(vals) * 100, 1) if vals else None,
            }
            for label, vals in buckets.items()
        },
        "note": "Compare actual_win_rate to the bucket label. If 70-84% bucket only wins 50%, the AI overrates its confidence.",
    }


# ─── Standings ──────────────────────────────────────────

@router.get("/standings/{sport}")
async def get_standings(sport: str, league: str = None):
    if sport == "nba":
        standings = await nba_service.get_standings()
        return {"sport": "nba", "standings": standings}

    elif sport == "nhl":
        standings = await nhl_service.get_standings()
        return {"sport": "nhl", "standings": standings}

    elif sport == "football" and league:
        league_id = LEAGUE_IDS.get(league)
        if league_id:
            standings = await football_service.get_standings(league_id)
            return {"sport": "football", "league": league, "standings": standings}

    raise HTTPException(400, "Provide sport and league")


# ─── Bet Tracking (SQLite-backed) ───────────────────────

# We persist bets to SQLite via the `bets` table defined in app/core/database.py.
# `match` and `bet_type` come from the BetRecord schema; we store `match` into
# `actual_score` is NULL until settled. We add a dedicated `match` column lazily
# via PRAGMA-checked migration so existing DBs keep working.

async def _ensure_match_column(db: aiosqlite.Connection) -> None:
    """Add a `match` column to the bets table if missing (lightweight migration)."""
    cur = await db.execute("PRAGMA table_info(bets)")
    cols = {row[1] for row in await cur.fetchall()}
    if "match" not in cols:
        await db.execute("ALTER TABLE bets ADD COLUMN match TEXT")
        await db.commit()


def _row_to_bet(row: aiosqlite.Row) -> dict:
    return {
        "id": row["id"],
        "match": row["match"],
        "bet_type": row["bet_type"],
        "pick": row["pick"],
        "odds": row["odds"],
        "stake": row["stake"],
        "potential_payout": row["potential_payout"],
        "result": row["result"],
        "actual_score": row["actual_score"],
        "profit_loss": row["profit_loss"],
        "placed_at": row["placed_at"],
        "settled_at": row["settled_at"],
    }


@router.post("/bets")
async def record_bet(bet: BetRecord):
    potential_payout = round(bet.odds * bet.stake, 2)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await _ensure_match_column(db)
        cur = await db.execute(
            """INSERT INTO bets (match, bet_type, pick, odds, stake, potential_payout, result, actual_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (bet.match, bet.bet_type, bet.pick, bet.odds, bet.stake,
             potential_payout, bet.result or "pending", bet.actual_score),
        )
        await db.commit()
        bet_id = cur.lastrowid
        row = await (await db.execute("SELECT * FROM bets WHERE id = ?", (bet_id,))).fetchone()
    return {"message": "Bet recorded", "bet": _row_to_bet(row)}


@router.get("/bets")
async def get_bets():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await _ensure_match_column(db)
        rows = await (await db.execute("SELECT * FROM bets ORDER BY id DESC")).fetchall()
    return {"bets": [_row_to_bet(r) for r in rows]}


@router.delete("/bets")
async def clear_bets():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM bets")
        await db.commit()
    return {"message": "All bets cleared"}


@router.put("/bets/{bet_id}/settle")
async def settle_bet(bet_id: int, result: str, actual_score: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await _ensure_match_column(db)
        row = await (await db.execute("SELECT * FROM bets WHERE id = ?", (bet_id,))).fetchone()
        if row is None:
            raise HTTPException(404, "Bet not found")

        if result == "won":
            profit_loss = round((row["potential_payout"] or 0) - (row["stake"] or 0), 2)
        elif result == "lost":
            profit_loss = -(row["stake"] or 0)
        else:
            profit_loss = 0

        await db.execute(
            """UPDATE bets
               SET result = ?, actual_score = ?, profit_loss = ?, settled_at = datetime('now')
               WHERE id = ?""",
            (result, actual_score, profit_loss, bet_id),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM bets WHERE id = ?", (bet_id,))).fetchone()
    return {"message": "Bet settled", "bet": _row_to_bet(row)}


@router.get("/bets/summary")
async def bet_summary():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            """SELECT
                 COUNT(*) AS total,
                 SUM(CASE WHEN result = 'won'     THEN 1 ELSE 0 END) AS won,
                 SUM(CASE WHEN result = 'lost'    THEN 1 ELSE 0 END) AS lost,
                 SUM(CASE WHEN result = 'pending' THEN 1 ELSE 0 END) AS pending,
                 COALESCE(SUM(stake), 0)       AS total_staked,
                 COALESCE(SUM(profit_loss), 0) AS total_profit
               FROM bets"""
        )).fetchone()

    won = row["won"] or 0
    lost = row["lost"] or 0
    pending = row["pending"] or 0
    total_staked = row["total_staked"] or 0
    total_profit = row["total_profit"] or 0

    return {
        "total_bets": row["total"] or 0,
        "won": won,
        "lost": lost,
        "pending": pending,
        "win_rate": round(won / (won + lost) * 100, 1) if (won + lost) > 0 else 0,
        "total_staked": round(total_staked, 2),
        "total_profit": round(total_profit, 2),
        "roi": round(total_profit / total_staked * 100, 1) if total_staked > 0 else 0,
    }
