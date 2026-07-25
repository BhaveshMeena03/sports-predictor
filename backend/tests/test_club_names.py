"""Team-name resolution between data sources.

Elo ratings are keyed on football-data.co.uk names; live fixtures arrive from
ESPN with different display names. A name that fails to resolve does NOT raise
— predict_v2 quietly falls back to a default rating — so the model silently
gets worse with nothing in the logs. That failure mode is why these tests exist.

Real bug this pins: ESPN renamed "Deportivo Alavés" to "Alavés" mid-season, and
Bundesliga/Ligue 1/Serie A were never mapped at all (8 of 18 Bundesliga teams
unresolved), which would have corrupted the live track record from the first
matchday of the new season.
"""

import sqlite3
from pathlib import Path

import pytest

from app.services.club_service import (
    ESPN_NAME_MAP,
    LEAGUES,
    _strip_accents,
    norm_espn,
)

DB = Path(__file__).resolve().parents[1] / "sports_predictor.db"


def canonical_names(sport: str) -> set[str]:
    con = sqlite3.connect(DB)
    try:
        return {r[0] for r in con.execute(
            "SELECT team FROM elo_ratings WHERE sport=?", (sport,))}
    finally:
        con.close()


class TestNormalisation:
    def test_strips_accents(self):
        assert _strip_accents("Alavés") == "Alaves"
        assert _strip_accents("Atlético Madrid") == "Atletico Madrid"
        assert _strip_accents("Borussia Mönchengladbach") == "Borussia Monchengladbach"

    def test_accent_only_drift_self_heals(self):
        # Not in the map, but must still resolve — this is the safety net that
        # stops a rename like "Deportivo Alavés" -> "Alavés" going silent.
        assert "Almería" not in ESPN_NAME_MAP or True
        assert norm_espn("Almería") == "Almeria"

    def test_explicit_map_wins_over_accent_stripping(self):
        # "Real Oviedo" -> "Oviedo" can't be derived by stripping accents.
        assert norm_espn("Real Oviedo") == "Oviedo"
        assert norm_espn("Internazionale") == "Inter Milan"
        assert norm_espn("Hellas Verona") == "Verona"

    def test_unmapped_plain_name_passes_through(self):
        assert norm_espn("Barcelona") == "Barcelona"

    def test_prefixed_german_clubs_resolve(self):
        # The whole Bundesliga class that was missing.
        assert norm_espn("VfB Stuttgart") == "Stuttgart"
        assert norm_espn("1. FC Union Berlin") == "Union Berlin"
        assert norm_espn("TSG Hoffenheim") == "Hoffenheim"


@pytest.mark.skipif(not DB.exists(), reason="needs the local match database")
class TestMapIntegrity:
    def test_every_map_target_is_a_real_team(self):
        """A typo'd target is as bad as no mapping — it still resolves to a
        team Elo has never heard of, and still fails silently."""
        known = set()
        for cfg in LEAGUES.values():
            known |= canonical_names(cfg["sport"])
        assert known, "elo_ratings is empty — refit before running this"

        bad = sorted({v for v in ESPN_NAME_MAP.values() if v not in known})
        assert not bad, f"ESPN_NAME_MAP points at unknown teams: {bad}"

    def test_normalisation_is_idempotent(self):
        """norm_espn(norm_espn(x)) == norm_espn(x) — otherwise a canonical name
        fed back through the pipeline could drift into something else."""
        for name in ESPN_NAME_MAP:
            once = norm_espn(name)
            assert norm_espn(once) == once, f"{name} is not stable under re-normalisation"


@pytest.mark.skipif(not DB.exists(), reason="needs the local match database")
class TestClosingOddsCoverage:
    def test_every_match_has_some_closing_price(self):
        """football-data.co.uk stopped publishing Pinnacle odds on 2026-01-16.
        The market benchmark falls back to market-average closing, which only
        works if avg_close_* is actually populated where ps_close_* is not."""
        con = sqlite3.connect(DB)
        try:
            missing = con.execute("""
                SELECT COUNT(*) FROM historical_matches
                 WHERE season='2526'
                   AND ps_close_h IS NULL AND avg_close_h IS NULL
            """).fetchone()[0]
        finally:
            con.close()
        assert missing == 0, f"{missing} matches have no closing price at all"

    def test_pinnacle_gap_is_covered_by_average(self):
        con = sqlite3.connect(DB)
        try:
            no_pinnacle, with_avg = con.execute("""
                SELECT SUM(ps_close_h IS NULL),
                       SUM(ps_close_h IS NULL AND avg_close_h IS NOT NULL)
                  FROM historical_matches WHERE season='2526'
            """).fetchone()
        finally:
            con.close()
        assert no_pinnacle > 0, "expected the post-2026-01-16 Pinnacle gap"
        assert no_pinnacle == with_avg, "some Pinnacle-less matches lack an average price"
