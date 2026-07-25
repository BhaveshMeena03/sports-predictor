"""Derived markets (totals, BTTS, correct score) from the Dixon-Coles matrix.

These come free — the score matrix is already built to produce 1X2 — but they
are easy to get subtly wrong, and a probability that doesn't sum to 1 or that
moves the wrong way with expected goals would be obvious to any buyer.
"""

import pytest

from app.services.intl_poisson import (
    TOTALS_LINES,
    _score_matrix,
    derive_markets,
)


class TestScoreMatrix:
    def test_is_a_probability_distribution(self):
        m = _score_matrix(1.6, 1.1)
        assert sum(p for row in m for p in row) == pytest.approx(1.0)
        assert all(p >= 0 for row in m for p in row)

    def test_truncation_tail_is_negligible_before_renormalising(self):
        """The grid is finite and the lost mass is renormalised away, which is
        only safe while that mass is tiny: truncation drops solely high-scoring
        outcomes, so a material tail biases every totals line downward.

        The worst case is a LOPSIDED fixture, not an even one — one large lambda
        has a much fatter tail than two moderate ones. At the old 10-goal cap
        this reached 2.8e-03, enough to show up at the 4 decimal places we
        report; MAX_GOALS=15 puts it at ~3e-06.
        """
        from app.services.intl_poisson import MAX_GOALS, _pois

        worst = 0.0
        for total in (1.3, 2.0, 2.85, 3.6):          # predict_v2 clamps to this range
            for frac in (0.5, 0.7, 0.9, 0.95):       # even .. very lopsided
                lh, la = total * frac, total * (1 - frac)
                mass = sum(_pois(h, lh) * _pois(a, la)
                           for h in range(MAX_GOALS) for a in range(MAX_GOALS))
                worst = max(worst, 1 - mass)
        assert worst < 1e-5, f"worst discarded tail {worst:.2e} biases totals downward"

    def test_higher_lambda_shifts_mass_to_higher_scores(self):
        low = derive_markets(_score_matrix(0.8, 0.6))
        high = derive_markets(_score_matrix(2.6, 2.1))
        assert high["totals"]["2.5"]["over"] > low["totals"]["2.5"]["over"]


class TestTotals:
    def test_over_and_under_are_complementary(self):
        mk = derive_markets(_score_matrix(1.7, 1.2))
        for line, v in mk["totals"].items():
            assert v["over"] + v["under"] == pytest.approx(1.0, abs=1e-3), line

    def test_over_probability_decreases_with_the_line(self):
        mk = derive_markets(_score_matrix(1.7, 1.2))
        overs = [mk["totals"][str(l)]["over"] for l in TOTALS_LINES]
        assert overs == sorted(overs, reverse=True)

    def test_all_configured_lines_present(self):
        mk = derive_markets(_score_matrix(1.5, 1.5))
        assert set(mk["totals"]) == {str(l) for l in TOTALS_LINES}


class TestBTTS:
    def test_complementary(self):
        mk = derive_markets(_score_matrix(1.4, 1.3))
        assert mk["btts"]["yes"] + mk["btts"]["no"] == pytest.approx(1.0, abs=1e-3)

    def test_lopsided_match_is_unlikely_to_see_both_score(self):
        """One team barely threatens => BTTS should be low."""
        mk = derive_markets(_score_matrix(2.5, 0.2))
        assert mk["btts"]["yes"] < 0.25

    def test_even_high_scoring_match_favours_both_scoring(self):
        mk = derive_markets(_score_matrix(1.8, 1.8))
        assert mk["btts"]["yes"] > 0.6


class TestCorrectScore:
    def test_sorted_by_descending_probability(self):
        cs = derive_markets(_score_matrix(1.6, 1.1))["correct_score"]
        assert [c["p"] for c in cs] == sorted((c["p"] for c in cs), reverse=True)

    def test_modal_score_matches_the_lambdas(self):
        """With xG ~2.1 vs ~0.7 the likeliest scoreline should be a home win."""
        top = derive_markets(_score_matrix(2.1, 0.7))["correct_score"][0]["score"]
        h, a = (int(x) for x in top.split("-"))
        assert h > a


class TestHonestLabelling:
    def test_flagged_as_uncalibrated(self):
        """The fitted alpha is trained on 1X2 outcomes. Applying it to a totals
        market would borrow a correction from a different question, so these are
        served raw and must say so."""
        mk = derive_markets(_score_matrix(1.5, 1.2))
        assert mk["calibrated"] is False
        assert "calibrated" in mk["note"].lower()
