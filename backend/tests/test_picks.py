"""Beat the Model: the properties that make the leaderboard mean anything.

A prediction game is only worth playing if picks are locked before kickoff and
cannot be revised afterwards. Everything else is presentation. These tests pin
that, plus the scoring maths that makes user scores comparable to the model's.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    import app.core.database as database
    import app.services.picks as picks

    path = str(tmp_path / "picks.db")
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setattr(picks, "DB_PATH", path)
    asyncio.run(picks.ensure_tables())
    return path


def _rows(path, sql, args=()):
    import sqlite3
    con = sqlite3.connect(path)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _future(hours=48):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _past(hours=2):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _tomorrow():
    return (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")


class TestVectorAndBrier:
    def test_confidence_becomes_a_probability_vector(self):
        from app.services.picks import vector_from

        v = vector_from("home", 0.60)
        assert v[0] == 0.60
        assert abs(sum(v) - 1.0) < 1e-9
        assert v[1] == v[2], "the remainder should split evenly"

    def test_confidence_is_clamped(self):
        from app.services.picks import MAX_CONFIDENCE, MIN_CONFIDENCE, vector_from

        assert vector_from("away", 0.999)[2] == MAX_CONFIDENCE
        assert vector_from("away", 0.01)[2] == MIN_CONFIDENCE

    def test_brier_matches_the_model_formula(self):
        """Divided by 3, like club_match_log. A different divisor would make
        the leaderboard silently incomparable to the track record."""
        from app.services.picks import brier

        assert brier([1.0, 0.0, 0.0], 0) == 0.0
        expected = ((0.6 - 1) ** 2 + 0.2 ** 2 + 0.2 ** 2) / 3
        assert abs(brier([0.6, 0.2, 0.2], 0) - expected) < 1e-9

    def test_confident_and_wrong_scores_worse_than_unsure_and_wrong(self):
        """The property that makes Brier the right metric for this game."""
        from app.services.picks import brier, vector_from

        cocky = brier(vector_from("home", 0.95), 2)
        humble = brier(vector_from("home", 0.40), 2)
        assert cocky > humble


class TestTheLock:
    def test_a_pick_before_kickoff_is_accepted(self, db):
        from app.services.picks import submit_pick

        out = asyncio.run(submit_pick(
            "alice", "premier_league", _tomorrow(), "Arsenal", "Chelsea",
            "home", 0.6, kickoff_utc=_future()))
        assert out["pick"] == "home"

    def test_a_pick_after_kickoff_is_refused(self, db):
        from app.services.picks import submit_pick

        with pytest.raises(ValueError, match="already kicked off"):
            asyncio.run(submit_pick(
                "alice", "premier_league", _tomorrow(), "Arsenal", "Chelsea",
                "home", 0.6, kickoff_utc=_past()))

    def test_a_past_fixture_is_refused_without_a_kickoff_time(self, db):
        from app.services.picks import submit_pick

        with pytest.raises(ValueError, match="in the past"):
            asyncio.run(submit_pick(
                "alice", "premier_league", "2020-01-01", "Arsenal", "Chelsea",
                "home", 0.6))

    def test_a_standing_pick_cannot_be_changed(self, db):
        """Without this the leaderboard proves nothing."""
        from app.services.picks import submit_pick

        asyncio.run(submit_pick("alice", "premier_league", _tomorrow(),
                                "Arsenal", "Chelsea", "home", 0.6,
                                kickoff_utc=_future()))
        with pytest.raises(ValueError, match="already picked"):
            asyncio.run(submit_pick("alice", "premier_league", _tomorrow(),
                                    "Arsenal", "Chelsea", "away", 0.9,
                                    kickoff_utc=_future()))
        row = _rows(db, "SELECT pick, p_home FROM user_picks")[0]
        assert row == ("home", 0.6)

    def test_two_players_can_pick_the_same_fixture(self, db):
        from app.services.picks import submit_pick

        for who in ("alice", "bob"):
            asyncio.run(submit_pick(who, "premier_league", _tomorrow(),
                                    "Arsenal", "Chelsea", "home", 0.6,
                                    kickoff_utc=_future()))
        assert len(_rows(db, "SELECT * FROM user_picks")) == 2


class TestScoring:
    def _fixture_result(self, db, hg, ag):
        """A scored row in club_match_log, which is where results come from."""
        import sqlite3
        con = sqlite3.connect(db)
        con.execute("""CREATE TABLE IF NOT EXISTS club_match_log (
            league TEXT, date TEXT, home TEXT, away TEXT,
            home_goals INTEGER, away_goals INTEGER,
            p_home REAL, p_draw REAL, p_away REAL,
            picked TEXT, correct INTEGER, brier REAL,
            PRIMARY KEY (league, date, home, away))""")
        con.execute("""INSERT OR REPLACE INTO club_match_log
            (league,date,home,away,home_goals,away_goals,p_home,p_draw,p_away,
             picked,correct,brier)
            VALUES ('premier_league','2026-08-20','Arsenal','Chelsea',?,?,
                    0.5,0.3,0.2,'Arsenal',1,0.11)""", (hg, ag))
        con.commit(); con.close()

    def _pick(self, db, player, outcome, conf):
        import sqlite3
        from app.services.picks import vector_from
        v = vector_from(outcome, conf)
        con = sqlite3.connect(db)
        con.execute("""INSERT INTO user_picks
            (player,league,date,home,away,pick,p_home,p_draw,p_away,submitted_at)
            VALUES (?,'premier_league','2026-08-20','Arsenal','Chelsea',?,?,?,?,
                    '2026-08-19T10:00:00+00:00')""", (player, outcome, *v))
        con.commit(); con.close()

    def test_a_pick_is_scored_from_its_stored_vector(self, db):
        from app.services.picks import brier, score_finished_picks, vector_from

        self._fixture_result(db, 2, 0)
        self._pick(db, "alice", "home", 0.6)
        assert asyncio.run(score_finished_picks())["scored"] == 1

        row = _rows(db, "SELECT correct, brier FROM user_picks")[0]
        assert row[0] == 1
        assert abs(row[1] - round(brier(vector_from("home", 0.6), 0), 4)) < 1e-4

    def test_an_unplayed_fixture_is_not_scored(self, db):
        from app.services.picks import score_finished_picks

        self._pick(db, "alice", "home", 0.6)   # no result row at all
        assert asyncio.run(score_finished_picks())["scored"] == 0
        assert _rows(db, "SELECT brier FROM user_picks")[0][0] is None

    def test_scoring_twice_does_not_double_count(self, db):
        from app.services.picks import score_finished_picks

        self._fixture_result(db, 1, 1)
        self._pick(db, "alice", "draw", 0.5)
        assert asyncio.run(score_finished_picks())["scored"] == 1
        assert asyncio.run(score_finished_picks())["scored"] == 0

    def test_leaderboard_compares_against_the_model(self, db):
        from app.services.picks import leaderboard, score_finished_picks

        self._fixture_result(db, 2, 0)
        self._pick(db, "sharp", "home", 0.8)    # confident and right
        self._pick(db, "wrong", "away", 0.8)    # confident and wrong
        asyncio.run(score_finished_picks())

        lb = asyncio.run(leaderboard(min_picks=1))
        assert lb["model"]["avg_brier"] == 0.11
        assert [e["player"] for e in lb["ranked"]] == ["sharp", "wrong"]
        assert lb["ranked"][0]["beats_model"] is True
        assert lb["ranked"][1]["beats_model"] is False

    def test_one_lucky_pick_does_not_top_the_table(self, db):
        """min_picks keeps a single 0.95 hit from outranking a season."""
        from app.services.picks import leaderboard, score_finished_picks

        self._fixture_result(db, 2, 0)
        self._pick(db, "oneshot", "home", 0.95)
        asyncio.run(score_finished_picks())

        lb = asyncio.run(leaderboard(min_picks=3))
        assert lb["ranked"] == []
        assert lb["unranked"] == 1
