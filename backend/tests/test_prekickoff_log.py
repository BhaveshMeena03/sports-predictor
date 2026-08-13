"""The track record's integrity property: a prediction, once written, is fixed.

Before this existed, /api/trackrecord claimed "every row was written BEFORE
kickoff" while both ingest paths skipped anything not already completed and
generated the probabilities at scoring time. No row had ever awaited a result.
The methodology was honest walk-forward, but the claim was not supported by
the data, and a record that overstates how it was produced is worth less than
a smaller one that doesn't.

These tests pin the behaviour that makes the claim true going forward:
a pre-logged prediction survives scoring unchanged.
"""

import asyncio

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A real sqlite file, so the schema migration is exercised too."""
    import app.core.database as database
    import app.services.club_service as cs

    path = str(tmp_path / "test.db")
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setattr(cs, "DB_PATH", path)
    asyncio.run(cs._ensure_tables())
    return path


def _rows(path, sql, args=()):
    import sqlite3

    con = sqlite3.connect(path)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


class TestSchema:
    def test_provenance_columns_exist(self, db):
        cols = {r[1] for r in _rows(db, "PRAGMA table_info(club_match_log)")}
        assert "prelogged" in cols
        assert "predicted_at" in cols

    def test_existing_rows_default_to_not_prelogged(self, db):
        """A row written by the old path must not claim to be a forward call."""
        import sqlite3

        con = sqlite3.connect(db)
        con.execute(
            """INSERT INTO club_match_log
               (league,date,home,away,home_goals,away_goals,
                p_home,p_draw,p_away,picked,correct,brier)
               VALUES ('premier_league','2026-08-20','A','B',1,0,
                       0.5,0.3,0.2,'A',1,0.1)""")
        con.commit()
        con.close()
        assert _rows(db, "SELECT prelogged FROM club_match_log")[0][0] == 0


class TestPrelogThenScore:
    """The core property: scoring fills in the result and touches nothing else."""

    def _prelog(self, db, probs=(0.60, 0.25, 0.15)):
        import sqlite3

        con = sqlite3.connect(db)
        con.execute(
            """INSERT INTO club_match_log
               (league,date,home,away,home_goals,away_goals,
                p_home,p_draw,p_away,picked,correct,brier,
                p_home_raw,p_draw_raw,p_away_raw,prelogged,predicted_at)
               VALUES ('premier_league','2026-08-20','Arsenal','Chelsea',
                       NULL,NULL,?,?,?,'Arsenal',NULL,NULL,?,?,?,1,
                       '2026-08-19T10:00:00+00:00')""",
            (*probs, *probs))
        con.commit()
        con.close()

    def test_a_prelogged_row_awaits_its_result(self, db):
        self._prelog(db)
        row = _rows(db, "SELECT home_goals, brier, prelogged FROM club_match_log")[0]
        assert row[0] is None and row[1] is None and row[2] == 1

    def test_scoring_keeps_the_original_probabilities(self, db, monkeypatch):
        """The whole point. If scoring re-predicts, the forward call is lost."""
        import app.services.club_service as cs

        self._prelog(db, probs=(0.60, 0.25, 0.15))

        async def fake_espn(league_key, ds):
            return [{"completed": True, "date": "2026-08-20", "home": "Arsenal",
                     "away": "Chelsea", "home_score": 2, "away_score": 0}]

        async def different_prediction(league, home, away):
            # Deliberately unlike the stored vector: if this leaks into the
            # row, the test fails and so does the record's integrity.
            return {"p_home": 0.10, "p_draw": 0.10, "p_away": 0.80,
                    "raw": [0.10, 0.10, 0.80]}

        monkeypatch.setattr(cs, "fetch_espn_league", fake_espn)
        monkeypatch.setattr(cs, "predict_club", different_prediction)
        monkeypatch.setattr(cs, "known_elo_teams", lambda sport: _async({"Arsenal", "Chelsea"}))

        class _Elo:
            def __init__(self, *a, **k): pass
            async def update_after_match(self, *a, **k): return {}

        monkeypatch.setattr(cs, "EloRatings", _Elo)
        asyncio.run(cs.daily_update_clubs(days_back=0))

        row = _rows(db, """SELECT p_home, p_draw, p_away, home_goals, away_goals,
                                  correct, brier, prelogged
                           FROM club_match_log""")[0]
        assert row[:3] == (0.60, 0.25, 0.15), "scoring overwrote the standing prediction"
        assert row[3] == 2 and row[4] == 0, "result was not filled in"
        assert row[5] == 1, "Arsenal was picked and won, should be correct"
        assert row[7] == 1, "prelogged flag was lost"
        # Brier against the ORIGINAL vector, not the fresh one.
        expected = ((0.60 - 1) ** 2 + 0.25 ** 2 + 0.15 ** 2) / 3
        assert abs(row[6] - round(expected, 4)) < 1e-4

    def test_rerunning_prelog_never_overwrites(self, db, monkeypatch):
        """A standing prediction must not be revisable after the fact."""
        import app.services.club_service as cs

        self._prelog(db, probs=(0.60, 0.25, 0.15))

        async def fixtures(days):
            return [{"league": "premier_league", "date": "2026-08-20",
                     "home": "Arsenal", "away": "Chelsea"}]

        async def new_prediction(league, home, away):
            return {"p_home": 0.20, "p_draw": 0.20, "p_away": 0.60,
                    "raw": [0.20, 0.20, 0.60]}

        import app.services.ask as ask
        monkeypatch.setattr(ask, "upcoming_fixtures", fixtures)
        monkeypatch.setattr(cs, "predict_club", new_prediction)
        asyncio.run(cs.prelog_upcoming_clubs(days_ahead=7))

        row = _rows(db, "SELECT p_home, p_draw, p_away FROM club_match_log")[0]
        assert row == (0.60, 0.25, 0.15), "a standing prediction was rewritten"

    def test_late_fixture_is_logged_but_marked_honestly(self, db, monkeypatch):
        """No standing prediction means prelogged=0, not a false claim."""
        import app.services.club_service as cs

        async def fake_espn(league_key, ds):
            if league_key != "premier_league":
                return []
            return [{"completed": True, "date": "2026-08-20", "home": "Spurs",
                     "away": "Everton", "home_score": 1, "away_score": 1}]

        async def pred(league, home, away):
            return {"p_home": 0.4, "p_draw": 0.3, "p_away": 0.3,
                    "raw": [0.4, 0.3, 0.3]}

        class _Elo:
            def __init__(self, *a, **k): pass
            async def update_after_match(self, *a, **k): return {}

        monkeypatch.setattr(cs, "fetch_espn_league", fake_espn)
        monkeypatch.setattr(cs, "predict_club", pred)
        monkeypatch.setattr(cs, "known_elo_teams", lambda sport: _async({"Spurs", "Everton"}))
        monkeypatch.setattr(cs, "EloRatings", _Elo)
        asyncio.run(cs.daily_update_clubs(days_back=0))

        row = _rows(db, "SELECT prelogged, brier FROM club_match_log")[0]
        assert row[0] == 0, "an ingest-time prediction claimed to be pre-kickoff"
        assert row[1] is not None, "it should still be scored"

    def test_an_already_scored_row_is_left_alone(self, db, monkeypatch):
        import app.services.club_service as cs
        import sqlite3

        con = sqlite3.connect(db)
        con.execute(
            """INSERT INTO club_match_log
               (league,date,home,away,home_goals,away_goals,
                p_home,p_draw,p_away,picked,correct,brier,prelogged)
               VALUES ('premier_league','2026-08-20','A','B',3,1,
                       0.5,0.3,0.2,'A',1,0.1,1)""")
        con.commit(); con.close()

        async def fake_espn(league_key, ds):
            return [{"completed": True, "date": "2026-08-20", "home": "A",
                     "away": "B", "home_score": 9, "away_score": 9}]

        monkeypatch.setattr(cs, "fetch_espn_league", fake_espn)
        monkeypatch.setattr(cs, "known_elo_teams", lambda sport: _async(set()))
        asyncio.run(cs.daily_update_clubs(days_back=0))

        row = _rows(db, "SELECT home_goals, away_goals, brier FROM club_match_log")[0]
        assert row == (3, 1, 0.1), "a settled row was rewritten"


async def _async(value):
    return value


class TestBasisLabel:
    """basis must describe the log as it is, including before any result lands."""

    def test_only_standing_predictions_reads_as_prekickoff(self):
        matches = [{"prelogged": 1, "brier": None}, {"prelogged": 1, "brier": None}]
        assert _basis(matches) == "prekickoff"

    def test_only_ingest_time_rows_read_as_backtest(self):
        assert _basis([{"prelogged": 0, "brier": 0.1}]) == "walk_forward_backtest"

    def test_a_mix_says_mixed(self):
        assert _basis([{"prelogged": 1, "brier": None},
                       {"prelogged": 0, "brier": 0.1}]) == "mixed"

    def test_empty_log(self):
        assert _basis([]) == "empty"


def _basis(matches):
    """Mirrors the expression in /api/trackrecord."""
    if not matches:
        return "empty"
    if all(m["prelogged"] for m in matches):
        return "prekickoff"
    if not any(m["prelogged"] for m in matches):
        return "walk_forward_backtest"
    return "mixed"
