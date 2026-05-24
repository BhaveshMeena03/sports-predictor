# Sports Predictor

A self-maintaining **ensemble sports-prediction system** built on free data, with every accuracy claim backed by a backtest. It combines four independent signals — **Elo ratings**, a **Dixon-Coles Poisson** goals model, an **LLM analyst**, and **market odds** — into calibrated match probabilities, served through a FastAPI backend and a Next.js dashboard.

> **The honest one-liner:** this is an *elite-calibration* tool (within ~0.003 Brier of Pinnacle's closing line) — **not** a money-printer. It cannot beat top-league closing lines for profit, and the backtests prove it. Its real edge is in **soft markets** (e.g. World Cup outrights) and in match insight.

## Why it's built this way

Most "prediction" projects quietly overfit and report a flattering accuracy number. This one is designed around the opposite principle: hold every model to a **backtest against real closing lines**, and report where it does *and does not* have an edge.

## Architecture

```
                ┌───────────────────── FastAPI backend ─────────────────────┐
   free data ──►│  Elo  ·  Dixon-Coles Poisson  ·  LLM analyst  ·  Market   │
   (results,    │                         │                                  │
    odds)       │                    Ensemble (calibrated probabilities)     │
                │                         │                                  │
                │   SQLite  ·  daily refresh scheduler  ·  backtest engine   │
                └─────────────────────────┼──────────────────────────────────┘
                                          │  REST API (/docs)
                                          ▼
                            Next.js dashboard (analyze · multi · tracker · fixtures)
```

### Backend (`backend/`, FastAPI + SQLite)

- `services/elo.py` — Elo rating engine
- `services/poisson.py` — Dixon-Coles Poisson goals model (fit per league)
- `services/ensemble.py` — blends the signals into calibrated probabilities
- `services/market.py`, `odds_service.py` — market-odds ingestion and comparison
- `services/ai_analyzer.py` — LLM-based qualitative match analysis
- `services/backtest.py`, `backtest_historical.py` — backtesting against historical closing lines
- `services/scheduler.py` — daily refresh (re-ingests results, refits the Poisson model)
- `services/worldcup.py`, `football_service.py`, `nba_nhl_service.py` — sport/competition data sources

### Frontend (`frontend/`, Next.js + React + Tailwind)

A dashboard for single-match analysis, multi-match comparison, a results tracker, and fixtures.

## Quick start

```bash
# Backend
cd backend
pip install -r requirements.txt
cp .env.example .env        # add your API keys (Anthropic, API-Football, Odds API)
python main.py              # http://localhost:8000  (Swagger docs at /docs)

# Frontend
cd frontend
npm install
npm run dev                 # http://localhost:3000
```

See [`HOW_TO_USE.md`](HOW_TO_USE.md) for the full workflow, one-time data setup, and natural-language query examples.

## Tech

FastAPI · Python · SQLite · Next.js · React · TypeScript · Tailwind CSS

## Notes

`.env`, the local SQLite database, `node_modules`, and build artifacts are intentionally excluded from version control.
