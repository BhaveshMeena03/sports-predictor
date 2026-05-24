# Sports Predictor — How To Use

A self-maintaining, **ensemble** sports prediction system (Elo + Dixon-Coles Poisson +
LLM + market odds), built on **free data**, with every accuracy claim backed by a backtest.

> **The honest one-liner:** This is an *elite-calibration* tool (within ~0.003 Brier of
> Pinnacle's closing line) — **not** a money-printer. It cannot beat top-league closing
> lines for profit (proven). Its real edge is in **soft markets** like World Cup outrights
> and match insight. It tells you the truth about where it can and can't make money.

---

## 1. Quick start

```bash
cd backend
python main.py            # starts on http://localhost:8000
# API docs (Swagger):     http://localhost:8000/docs
```

On startup it: initialises the SQLite DB, and arms the **daily refresh scheduler**
(05:00 UTC — re-ingests international results + refits Dixon-Coles).

**One-time setup** (already done, but for a fresh machine):
```bash
curl -X POST "localhost:8000/api/historical/download"        # 5,330 club matches + odds
curl -X POST "localhost:8000/api/worldcup/ingest"            # 1,604 intl matches → Elo
curl -X POST "localhost:8000/api/poisson/fit/premier_league" # fit Dixon-Coles per league
```

> ⚠️ `.env` holds API keys (Anthropic, API-Football, Odds API). Keep it out of git.
> The backend uses `load_dotenv(override=True)` so an empty shell `ANTHROPIC_API_KEY`
> exported by another app won't shadow it.

---

## 2. Natural-language query guide

You don't need to remember endpoints — the `/quick-analyze` endpoint accepts plain-language
queries like these and routes them to the right call:

| You say… | What happens | Endpoint |
|---|---|---|
| "Analyze Arsenal vs Chelsea" | Full ensemble breakdown (Elo + Poisson + LLM blended) | `POST /analyze` |
| "Who wins the World Cup?" | 10,000-sim Monte Carlo | `POST /worldcup/simulate` |
| "Predict Spain vs Germany at the World Cup" | Neutral-venue national-team ensemble | `POST /analyze` (sport=international) |
| "Safe bets this week" | Ranked favourites across all sports | `GET /best-bets` |
| "Build a multi, my bankroll is £300" | Multi-bet + calibration-scaled Kelly stake | `POST /analyze-multi` |
| "Show me the dashboard" | Models, calibration, backtest proof | `GET /dashboard` |
| "How accurate has it been?" | Confidence calibration from settled bets | `GET /calibration` |
| "Record that bet / mark it won" | Persistent bet tracking | `POST /bets`, `PUT /bets/{id}/settle` |

---

## 3. Endpoint reference

### Prediction
- `POST /analyze` — **the main one.** Body: `{sport, league?, home_team, away_team, date?, venue?, extra_context?}`.
  Returns `ensemble` (production output), `base_models` (each model's view), `llm_analysis`.
  Supports `sport`: `football`, `international`, `nba`, `nhl`, `cricket`, `ipl`.
- `POST /analyze-multi` — multi-bet. Body adds `bankroll?` → returns Kelly stake.
- `POST /quick-analyze` — natural-language single query.
- `GET /worldcup/predict?home=&away=&neutral=true` — Elo-only intl prediction.

### World Cup
- `POST /worldcup/bootstrap` — seed 56 national teams with prior Elo.
- `POST /worldcup/ingest` — replace seeds with real results (WC2022, Euro24, Nations League, Copa, friendlies).
- `GET /worldcup/ratings?limit=48` — current Elo ladder.
- `POST /worldcup/simulate?n_sims=10000&groups_json=…` — Monte Carlo → advance/final/win %.

### Models & tuning
- `GET /elo/{sport}` · `GET /elo/{sport}/predict?home=&away=` — Elo ladder / quick predict.
- `POST /poisson/fit/{league_key}` — (re)fit Dixon-Coles for a league.
- `GET|PUT /ensemble/weights/{sport}` — view/tune blend weights + shrinkage.

### Proof & validation
- `GET /dashboard` — system status + live calibration + recent backtests.
- `GET /calibration` — confidence-bucket hit rate from settled bets.
- `POST /backtest?leagues=&pipelines=&matches_per_league=` — Brier/log-loss backtest (API-Football data).
- `POST /historical/download` · `POST /historical/backtest?league=&value_threshold=` — ROI backtest (closing odds).

### Data & ops
- `GET /best-bets?max_odds=&min_odds=&limit=` · `GET /upcoming-with-odds` · `GET /odds/{sport}`.
- `GET /fixtures` · `GET /standings/{sport}`.
- `GET /quota` — external API budget used.
- `GET /scheduler/status` · `POST /scheduler/run-now` — background refresh job.
- Bets: `POST /bets`, `GET /bets`, `DELETE /bets`, `PUT /bets/{id}/settle`, `GET /bets/summary`.

---

## 4. The honest scorecard (read this)

Measured on real data — not vibes:

| Claim | Status | Evidence |
|---|---|---|
| Ensemble is well-calibrated | ✅ | Brier 0.196 vs Pinnacle 0.193 (EPL), 0.187 vs 0.185 (La Liga) |
| Ensemble beats any single model | ✅ | 240-match backtest: ensemble 0.2115 < Elo 0.2154 < Poisson 0.2167 |
| LLM alone is over-confident | ✅ | Worst-calibrated pipeline; corrected by down-weighting to 15% |
| Can beat top-league closing lines for profit | ❌ | 342-match ROI backtest: value bets lost at every threshold |
| Edge exists in soft markets | 🎯 | Untested live, but the thesis: WC outrights/group qual are reputation-priced |

**Bottom line:** trust it for *insight and calibration*. For betting, only chase
divergence in **soft markets**, stake small, and track results via `/calibration`.

---

## 5. Maintenance

- **Automatic:** daily 05:00 UTC job re-ingests intl results + refits Dixon-Coles.
- **Manual refresh:** `POST /scheduler/run-now`.
- **Before the World Cup:** plug in the real group draw via `groups_json`, and run
  `/worldcup/ingest` after spring friendlies.
- **New club season:** `POST /poisson/fit/{league}?season=YYYY` once results exist.

---

## 6. The year-long roadmap — making it genuinely better

Ordered by **impact per effort**. The single biggest ceiling right now is **free data**.

### Phase 1 — Break the data ceiling (biggest lever)
- [ ] **Paid API-Football tier (~$19/mo)** — current-season data, lineups, player stats.
      Removes the "stuck on 2024 season" limit; live form during the WC.
- [ ] **xG data** (Understat / FBref scrape) — expected goals predict better than raw goals.
      Feed xG into Dixon-Coles instead of actual goals → less noise, sharper ratings.
- [ ] **Pre-kickoff lineup/injury feed** — re-analyze 2h out when team news drops
      (the scheduler hook already exists; needs a data source).

### Phase 2 — Smarter models
- [ ] **Time-weighted Dixon-Coles** — exponential decay so recent matches matter more
      (this is in the original 1997 paper; we skipped it). Easy, meaningful win.
- [ ] **Glicko-2 instead of Elo** — tracks rating *uncertainty*, better for teams with
      few matches (huge for international football).
- [ ] **Derive O/U, BTTS, Asian Handicap** — Dixon-Coles already produces a full score
      matrix; these markets are free to compute and often softer than 1X2.
- [ ] **Probability recalibration** — isotonic regression / Platt scaling on the
      ensemble output, fit per-league. Squeezes out the last calibration gains.

### Phase 3 — Validate like a pro
- [ ] **Walk-forward backtesting** — proper time-series CV (no peeking), not a single split.
- [ ] **Closing Line Value (CLV) tracking** — the north-star metric. Did our pick beat
      the *closing* line? Beating CLV reliably ⇒ long-term profit, even before ROI shows it.
- [ ] **Model versioning + A/B** — tag each prediction with the model version that made
      it, compare versions on live results.

### Phase 4 — Reach & polish
- [ ] **Frontend** (you wanted this) — dashboard, match analyzer, simulator, bet tracker UI.
- [ ] **Daily notification** — top value spots emailed/pushed each morning.
- [ ] **More sports done right** — NBA/NHL Elo + sport-specific models; cricket needs
      a proper model (currently LLM-only).
- [ ] **Postgres + tests** — if it grows beyond personal use, move off SQLite and add pytest.

### The mindset that keeps it honest
1. **Every change must beat the current backtest** — no faith-based "improvements."
2. **Calibration first, accuracy second** — a well-calibrated 55% is worth more than a
   cocky 60%.
3. **The market is the benchmark, not the enemy** — use it as a prior; bet only where you
   genuinely diverge in a soft market.
4. **Track CLV, not just wins** — short-run ROI is noise; CLV is signal.

---

*Built across 4 sessions, May 2026. Backend: FastAPI + SQLite + scikit/scipy.
Frontend: coming later. Every number in this doc is reproducible via the endpoints above.*
