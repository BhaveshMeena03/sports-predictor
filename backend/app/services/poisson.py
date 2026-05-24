"""
Dixon-Coles Poisson model for football 1X2 prediction.

This is the industry-standard football model (Dixon & Coles 1997). Models each
team with an attack rate (α) and a defense rate (β), plus a global home-advantage
parameter. Expected goals:
    λ_home = exp(α_home + β_away + home_adv)
    λ_away = exp(α_away + β_home)
Score probability = Poisson(λ_home, x) × Poisson(λ_away, y) × τ(x,y,λ,μ,ρ)
where τ is the low-score correction (fixes Poisson under-predicting 0-0 / 1-1 etc).

Parameters fit via MLE with scipy.optimize. The constraint Σα = 0 keeps the
model identifiable (otherwise we could shift attack & defense by any constant).

We persist fitted parameters per-(sport, league, season) to SQLite as JSON so
we don't refit on every prediction.
"""

import json
import logging
import math
import time
import aiosqlite
import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson
from app.core.database import DB_PATH

log = logging.getLogger(__name__)


# ─── DB persistence ──────────────────────────────────────────────

async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS poisson_models (
                sport TEXT NOT NULL,
                league TEXT NOT NULL,
                season INTEGER NOT NULL,
                params_json TEXT NOT NULL,
                n_matches INTEGER NOT NULL,
                home_adv REAL,
                rho REAL,
                fitted_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (sport, league, season)
            );
        """)
        await db.commit()


async def save_model(sport: str, league: str, season: int, model: "DixonColes", n_matches: int) -> None:
    await _ensure_table()
    params = {
        "teams": model.teams,
        "x": model.params.tolist(),
        "home_adv": float(model.home_adv()),
        "rho": float(model.rho()),
    }
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO poisson_models (sport, league, season, params_json, n_matches, home_adv, rho)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(sport, league, season) DO UPDATE SET
                 params_json=excluded.params_json, n_matches=excluded.n_matches,
                 home_adv=excluded.home_adv, rho=excluded.rho,
                 fitted_at=datetime('now')""",
            (sport, league, season, json.dumps(params), n_matches, params["home_adv"], params["rho"]),
        )
        await db.commit()


async def load_model(sport: str, league: str, season: int) -> "DixonColes | None":
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT params_json FROM poisson_models WHERE sport=? AND league=? AND season=?",
            (sport, league, season),
        )
        row = await cur.fetchone()
    if not row:
        return None
    params = json.loads(row[0])
    m = DixonColes(params["teams"])
    m.params = np.array(params["x"])
    return m


# ─── Model ───────────────────────────────────────────────────────

class DixonColes:
    """Fitted via L-BFGS-B. Outputs P(home), P(draw), P(away)."""

    def __init__(self, teams: list[str]):
        self.teams = sorted(set(teams))
        self.team_idx = {t: i for i, t in enumerate(self.teams)}
        self.n = len(self.teams)
        self.params: np.ndarray | None = None

    # ─── Parameter packing ───────────────────────────────────

    def _unpack(self, x: np.ndarray):
        """Param vector layout:
          [0 : n-1]       = attack ratings for teams 0..n-2 (last = -sum, enforces Σα=0)
          [n-1 : 2n-1]    = defense ratings for all n teams
          [2n-1]          = home advantage (additive in log space)
          [2n]            = ρ (low-score correction, typically negative ~ -0.1)
        """
        attacks = np.zeros(self.n)
        attacks[: self.n - 1] = x[: self.n - 1]
        attacks[-1] = -attacks[: self.n - 1].sum()
        defenses = x[self.n - 1 : 2 * self.n - 1]
        home_adv = x[2 * self.n - 1]
        rho = x[2 * self.n]
        return attacks, defenses, home_adv, rho

    def home_adv(self) -> float:
        return float(self._unpack(self.params)[2])

    def rho(self) -> float:
        return float(self._unpack(self.params)[3])

    # ─── Low-score correction ──────────────────────────────

    @staticmethod
    def _tau(hg: int, ag: int, lam: float, mu: float, rho: float) -> float:
        if hg == 0 and ag == 0:
            return max(1 - lam * mu * rho, 1e-9)
        if hg == 0 and ag == 1:
            return max(1 + lam * rho, 1e-9)
        if hg == 1 and ag == 0:
            return max(1 + mu * rho, 1e-9)
        if hg == 1 and ag == 1:
            return max(1 - rho, 1e-9)
        return 1.0

    # ─── Likelihood ───────────────────────────────────────

    def _neg_log_likelihood(self, x: np.ndarray, matches: list[tuple]) -> float:
        attacks, defenses, home_adv, rho = self._unpack(x)
        ll = 0.0
        for h, a, hg, ag in matches:
            lam = math.exp(attacks[h] + defenses[a] + home_adv)
            mu = math.exp(attacks[a] + defenses[h])
            tau = self._tau(hg, ag, lam, mu, rho)
            ll += math.log(tau) + poisson.logpmf(hg, lam) + poisson.logpmf(ag, mu)
        return -ll

    # ─── Fit ──────────────────────────────────────────────

    def fit(self, matches: list[dict], verbose: bool = False) -> dict:
        """matches: [{home, away, home_goals, away_goals}, ...]"""
        idx_matches = []
        for m in matches:
            h = self.team_idx.get(m["home"])
            a = self.team_idx.get(m["away"])
            if h is None or a is None:
                continue
            idx_matches.append((h, a, int(m["home_goals"]), int(m["away_goals"])))
        if not idx_matches:
            raise ValueError("No matches to fit on (team names don't match)")

        # Start: tiny random attacks/defenses, home_adv=0.25, rho=-0.1
        rng = np.random.default_rng(seed=42)
        x0 = np.concatenate([
            rng.normal(0, 0.05, self.n - 1),  # attack 0..n-2
            rng.normal(0, 0.05, self.n),      # defense 0..n-1
            [0.25, -0.10],                    # home_adv, rho
        ])

        t = time.time()
        result = minimize(
            self._neg_log_likelihood,
            x0,
            args=(idx_matches,),
            method="L-BFGS-B",
            options={"maxiter": 300, "ftol": 1e-7},
        )
        self.params = result.x
        if verbose:
            log.info("DC fit: n_matches=%d, log_lik=%.2f, %.2fs",
                     len(idx_matches), -result.fun, time.time() - t)
        return {
            "n_matches": len(idx_matches),
            "n_teams": self.n,
            "log_likelihood": -float(result.fun),
            "home_adv": self.home_adv(),
            "rho": self.rho(),
            "duration_seconds": round(time.time() - t, 2),
            "converged": bool(result.success),
        }

    # ─── Predict ──────────────────────────────────────────

    def predict_1x2(self, home: str, away: str, max_goals: int = 10) -> dict | None:
        """Score matrix → P(home), P(draw), P(away). max_goals=10 captures >99.9% of mass."""
        if self.params is None:
            return None
        h = self.team_idx.get(home)
        a = self.team_idx.get(away)
        if h is None or a is None:
            return None
        attacks, defenses, home_adv, rho = self._unpack(self.params)
        lam = math.exp(attacks[h] + defenses[a] + home_adv)
        mu = math.exp(attacks[a] + defenses[h])

        # Pre-compute Poisson PMFs
        hg_pmf = poisson.pmf(np.arange(max_goals), lam)
        ag_pmf = poisson.pmf(np.arange(max_goals), mu)

        p_home = p_draw = p_away = 0.0
        for hg in range(max_goals):
            for ag in range(max_goals):
                p = hg_pmf[hg] * ag_pmf[ag] * self._tau(hg, ag, lam, mu, rho)
                if hg > ag:
                    p_home += p
                elif hg < ag:
                    p_away += p
                else:
                    p_draw += p
        # Normalise (tau makes the matrix slightly off-unity)
        total = p_home + p_draw + p_away
        return {
            "p_home": round(float(p_home / total), 4),
            "p_draw": round(float(p_draw / total), 4),
            "p_away": round(float(p_away / total), 4),
            "expected_home_goals": round(float(lam), 3),
            "expected_away_goals": round(float(mu), 3),
        }
