"""Calibration: per-sport alphas, reliability curves, Brier decomposition.

Two real bugs pinned here.

1. One global alpha for every sport. It was fitted on 104 World Cup matches
   (national teams, neutral venues) and applied to club football too. Measured
   on 2025-26, the domains miscalibrate in different directions and by
   different amounts (EPL 0.88, La Liga 1.12), so the shared value pushed some
   leagues the wrong way.

2. Self-referential fitting. predict_v2 applies the stored alpha, so fitting on
   its output composes with the alpha already in force: 0.90 * 0.98 = 0.88 on
   the next pass, and further each time. Calibration must be fitted against raw
   model output.
"""

import asyncio

import pytest

from app.services import calibration_layer
from app.services.calibration_report import (
    brier_decomposition,
    fit_shrinkage,
    reliability_curve,
)


def sample(probs, outcome, market=None):
    s = {"probs": probs, "probs_raw": probs, "outcome": outcome}
    if market:
        s["market"] = market
    return s


class TestPerSportIsolation:
    def setup_method(self):
        calibration_layer._state.clear()

    def teardown_method(self):
        calibration_layer._state.clear()

    def test_unfitted_sport_is_left_alone(self):
        """The core safety property: an unfitted sport must NOT inherit another
        sport's correction. Being confidently wrong is worse than uncalibrated."""
        calibration_layer._state["international"] = {"alpha": 0.90, "n": 104}
        probs = [0.65, 0.22, 0.13]
        assert calibration_layer.apply(probs, sport="club_epl") == pytest.approx(probs)

    def test_each_sport_uses_its_own_alpha(self):
        calibration_layer._state.update({
            "international": {"alpha": 0.90, "n": 104},   # overconfident, shrink
            "club_laliga": {"alpha": 1.12, "n": 380},     # underconfident, sharpen
        })
        probs = [0.65, 0.22, 0.13]
        shrunk = calibration_layer.apply(probs, sport="international")
        sharpened = calibration_layer.apply(probs, sport="club_laliga")
        assert shrunk[0] < probs[0], "alpha<1 should pull toward uniform"
        assert sharpened[0] > probs[0], "alpha>1 should push away from uniform"

    def test_explicit_alpha_overrides_stored(self):
        calibration_layer._state["club_epl"] = {"alpha": 0.5, "n": 380}
        out = calibration_layer.apply([0.65, 0.22, 0.13], alpha=1.0, sport="club_epl")
        assert out == pytest.approx([0.65, 0.22, 0.13])

    def test_output_is_always_a_distribution(self):
        for alpha in (0.4, 0.9, 1.0, 1.3):
            out = calibration_layer.apply([0.65, 0.22, 0.13], alpha=alpha)
            assert sum(out) == pytest.approx(1.0)
            assert all(p >= 0 for p in out)


class TestShrinkageFit:
    def test_detects_overconfidence(self):
        """Model always claims 90% home; home actually wins ~half the time."""
        samples = ([sample([0.9, 0.05, 0.05], 0)] * 50
                   + [sample([0.9, 0.05, 0.05], 2)] * 50)
        assert fit_shrinkage(samples)["alpha"] < 1.0

    def test_degenerate_fit_defaults_to_no_adjustment(self):
        """A uniform prediction is invariant under shrink-toward-uniform, so
        every alpha scores identically. The tie must break to 1.0 rather than
        whichever grid end came first — otherwise the fit reports a large
        correction the data never supported."""
        samples = [sample([1 / 3, 1 / 3, 1 / 3], i % 3) for i in range(90)]
        assert fit_shrinkage(samples)["alpha"] == 1.0

    def test_well_calibrated_model_barely_moves(self):
        """Stated 50/25/25 matched by a 50/25/25 outcome split."""
        samples = ([sample([0.5, 0.25, 0.25], 0)] * 50
                   + [sample([0.5, 0.25, 0.25], 1)] * 25
                   + [sample([0.5, 0.25, 0.25], 2)] * 25)
        assert fit_shrinkage(samples)["alpha"] == pytest.approx(1.0, abs=0.1)

    def test_fitting_is_not_self_referential(self):
        """The drift bug: predict_v2 applies the stored alpha, so fitting on its
        output composes with the alpha already in force and the value walks on
        every refit. Fitting the raw vector is what makes it stable.

        Data is chosen to sit mid-grid — a saturated fit would pin both keys to
        the grid floor and hide the difference.
        """
        raw = [0.55, 0.27, 0.18]
        applied = 0.85
        calibrated = calibration_layer.apply(raw, alpha=applied)
        # Outcomes mildly favouring home: enough signal to land off the grid edge.
        outcomes = [0] * 42 + [1] * 30 + [2] * 28
        samples = [{"probs": calibrated, "probs_raw": raw, "outcome": o}
                   for o in outcomes]

        on_raw = fit_shrinkage(samples, key="probs_raw")["alpha"]
        on_served = fit_shrinkage(samples, key="probs")["alpha"]

        assert on_raw == fit_shrinkage(samples, key="probs_raw")["alpha"], "must be stable"
        assert on_raw != on_served, (
            "fitting the served vector must differ from fitting the raw one — "
            "that difference IS the drift this guards against")
        # Refitting on served output roughly reproduces alpha/applied: the
        # composition that made EPL walk 0.98 -> 0.88.
        assert on_served > on_raw


class TestReliabilityCurve:
    def test_perfectly_calibrated_data_has_no_gap(self):
        """Stated 50% on exactly half the outcomes => gap ~0."""
        samples = ([sample([0.5, 0.25, 0.25], 0)] * 50
                   + [sample([0.5, 0.25, 0.25], 1)] * 25
                   + [sample([0.5, 0.25, 0.25], 2)] * 25)
        home_bin = [b for b in reliability_curve(samples)
                    if b["predicted"] == pytest.approx(0.5, abs=0.01)]
        assert home_bin and abs(home_bin[0]["gap"]) < 0.02

    def test_overconfidence_shows_positive_gap(self):
        samples = ([sample([0.9, 0.05, 0.05], 0)] * 50
                   + [sample([0.9, 0.05, 0.05], 2)] * 50)
        top = [b for b in reliability_curve(samples) if b["predicted"] > 0.8]
        assert top and top[0]["gap"] > 0.3, "claimed 90%, delivered 50%"

    def test_every_outcome_contributes_a_point(self):
        samples = [sample([0.5, 0.25, 0.25], 0)] * 10
        assert sum(b["n"] for b in reliability_curve(samples)) == 30


class TestBrierDecomposition:
    def test_identity_holds_within_binning_error(self):
        samples = [sample([0.5, 0.3, 0.2], i % 3) for i in range(120)]
        d = brier_decomposition(samples)
        # Exact only if probabilities are constant within a bin; the residual is
        # within-bin variance and must stay small relative to the Brier score.
        assert abs(d["identity_check"]) < 0.01 * max(d["brier"], 1e-9) + 0.005

    def test_uninformative_model_has_no_resolution(self):
        """Always predicting the base rate is reliable but useless — resolution
        near zero is what distinguishes it from a genuinely skilful model."""
        samples = [sample([1 / 3, 1 / 3, 1 / 3], i % 3) for i in range(90)]
        assert brier_decomposition(samples)["resolution"] < 0.001

    def test_uncertainty_is_model_independent(self):
        """Uncertainty describes the outcomes, so two different models scored on
        the same matches must report the same value — that's what makes it a
        fair baseline."""
        outcomes = [i % 3 for i in range(90)]
        a = [sample([0.5, 0.3, 0.2], o) for o in outcomes]
        b = [sample([0.2, 0.3, 0.5], o) for o in outcomes]
        assert (brier_decomposition(a)["uncertainty"]
                == brier_decomposition(b)["uncertainty"])

    def test_empty_input_is_not_a_crash(self):
        assert brier_decomposition([]) == {}


class TestLogTableAllowlist:
    def test_rejects_table_names_outside_the_allowlist(self):
        """fit() interpolates the table name into SQL, so the allowlist is the
        thing standing between that and an injection point."""
        with pytest.raises(ValueError):
            asyncio.run(calibration_layer.fit("x", "wc_match_log; DROP TABLE bets--"))

    def test_accepts_known_tables(self):
        assert "wc_match_log" in calibration_layer.ALLOWED_LOG_TABLES
        assert "club_match_log" in calibration_layer.ALLOWED_LOG_TABLES
