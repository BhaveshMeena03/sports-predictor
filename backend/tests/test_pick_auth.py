"""Signed picks: the properties that make a wallet identity worth having.

A signature is only meaningful if it covers the whole prediction. Signing a
bare nonce and submitting the pick alongside it is the usual way this gets
built wrong -- it proves someone controls a wallet, and proves nothing about
what they predicted.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

ACCT = Account.from_key("0x" + "42" * 32)
OTHER = Account.from_key("0x" + "77" * 32)

FIXTURE = dict(league="la_liga", date="2026-08-20", home="Alaves",
               away="Getafe", pick="home", confidence=0.62)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sign(acct, **over):
    from app.services.pick_auth import build_message

    fields = {**FIXTURE, "issued": _now(), **over}
    msg = build_message(**fields)
    sig = acct.sign_message(encode_defunct(text=msg)).signature.hex()
    if not sig.startswith("0x"):
        sig = "0x" + sig
    return fields, sig


class TestSignatureCoversThePrediction:
    def test_a_valid_signature_verifies(self):
        from app.services.pick_auth import verify

        fields, sig = _sign(ACCT)
        assert verify(address=ACCT.address, signature=sig, **fields) == \
            ACCT.address.lower()

    def test_changing_the_pick_after_signing_is_caught(self):
        """Sign 'home', file 'away'. The whole point of covering the payload."""
        from app.services.pick_auth import verify

        fields, sig = _sign(ACCT)
        tampered = {**fields, "pick": "away"}
        with pytest.raises(ValueError, match="does not match"):
            verify(address=ACCT.address, signature=sig, **tampered)

    def test_changing_the_confidence_is_caught(self):
        from app.services.pick_auth import verify

        fields, sig = _sign(ACCT)
        with pytest.raises(ValueError, match="does not match"):
            verify(address=ACCT.address, signature=sig,
                   **{**fields, "confidence": 0.95})

    def test_changing_the_fixture_is_caught(self):
        from app.services.pick_auth import verify

        fields, sig = _sign(ACCT)
        with pytest.raises(ValueError, match="does not match"):
            verify(address=ACCT.address, signature=sig,
                   **{**fields, "home": "Real Madrid"})

    def test_someone_elses_signature_cannot_be_claimed(self):
        from app.services.pick_auth import verify

        fields, sig = _sign(OTHER)
        with pytest.raises(ValueError, match="does not match"):
            verify(address=ACCT.address, signature=sig, **fields)

    def test_garbage_signature_is_refused(self):
        from app.services.pick_auth import verify

        fields, _ = _sign(ACCT)
        with pytest.raises(ValueError):
            verify(address=ACCT.address, signature="0xdeadbeef", **fields)


class TestFreshness:
    def test_an_expired_signature_is_refused(self):
        from app.services.pick_auth import MAX_AGE, verify

        stale = (datetime.now(timezone.utc) - MAX_AGE - timedelta(minutes=1)).isoformat()
        fields, sig = _sign(ACCT, issued=stale)
        with pytest.raises(ValueError, match="expired"):
            verify(address=ACCT.address, signature=sig, **fields)

    def test_a_future_timestamp_is_refused(self):
        from app.services.pick_auth import verify

        ahead = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        fields, sig = _sign(ACCT, issued=ahead)
        with pytest.raises(ValueError, match="future"):
            verify(address=ACCT.address, signature=sig, **fields)

    def test_a_small_clock_skew_is_tolerated(self):
        """Rejecting a client 30s ahead would fail honest users constantly."""
        from app.services.pick_auth import verify

        ahead = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        fields, sig = _sign(ACCT, issued=ahead)
        assert verify(address=ACCT.address, signature=sig, **fields)


class TestStorage:
    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        import app.core.database as database
        import app.services.picks as picks

        path = str(tmp_path / "p.db")
        monkeypatch.setattr(database, "DB_PATH", path)
        monkeypatch.setattr(picks, "DB_PATH", path)
        asyncio.run(picks.ensure_tables())
        return path

    def test_an_address_is_a_valid_player_id(self, db):
        from app.services.picks import normalise_player

        assert normalise_player(ACCT.address) == ACCT.address.lower()

    def test_a_signed_pick_is_marked_verified(self, db):
        import sqlite3
        from app.services.picks import submit_pick

        out = asyncio.run(submit_pick(
            ACCT.address, "la_liga", "2099-01-01", "A", "B", "home", 0.6,
            verified=True, signature="0xabc"))
        assert out["verified"] is True

        con = sqlite3.connect(db)
        row = con.execute("SELECT verified, signature FROM user_picks").fetchone()
        con.close()
        assert row == (1, "0xabc")

    def test_an_unsigned_pick_is_not_marked_verified(self, db):
        from app.services.picks import submit_pick

        out = asyncio.run(submit_pick(
            "alice", "la_liga", "2099-01-01", "A", "B", "home", 0.6))
        assert out["verified"] is False

    def test_leaderboard_marks_verified_players_apart(self, db):
        import sqlite3
        from app.services.picks import leaderboard, score_finished_picks

        # Picks first, THEN the result. The other order is refused on purpose:
        # submitting a pick for a fixture that already has a result is the
        # exploit the peek guard exists to stop.
        asyncio.run(submit_pick_direct(db, ACCT.address.lower(), 1))
        asyncio.run(submit_pick_direct(db, "anon", 0))

        con = sqlite3.connect(db)
        con.execute("""CREATE TABLE club_match_log (
            league TEXT, date TEXT, home TEXT, away TEXT,
            home_goals INTEGER, away_goals INTEGER, brier REAL,
            PRIMARY KEY (league, date, home, away))""")
        con.execute("""INSERT INTO club_match_log VALUES
            ('la_liga','2026-08-20','A','B',2,0,0.11)""")
        con.commit(); con.close()

        asyncio.run(score_finished_picks())

        lb = asyncio.run(leaderboard(min_picks=1))
        by = {e["player"]: e["verified"] for e in lb["ranked"]}
        assert by[ACCT.address.lower()] is True
        assert by["anon"] is False


async def submit_pick_direct(db, player, verified):
    from app.services.picks import submit_pick

    await submit_pick(player, "la_liga", "2026-08-20", "A", "B", "home", 0.6,
                      verified=bool(verified), signature="0xsig" if verified else None)
