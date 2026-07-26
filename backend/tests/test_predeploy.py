"""The three gaps that had to close before any public deploy.

1. GET /calibration/reliability used to run a full-season backtest per request
   — a CPU-exhaustion vector, and worse: the backtest DELETEs and rebuilds
   elo_ratings, so public traffic was mutating model state and two concurrent
   calls could interleave rebuilds of the same table. Now a pure read of a
   report stored at (admin-gated) fit time.

2. Per-minute limits bound burst rate, not spend: 20/min still compounds to
   28,800 LLM calls/day. DailyBudget is the absolute kill-switch.

3. Public /analyze traffic wrote into the same predictions log the calibration
   tracker reports from — strangers couldn't change the model, but they could
   pollute the published numbers. Rows are now labelled owner/public and the
   tracker aggregates owner rows only.
"""

import asyncio
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.core import security
from app.core.security import DailyBudget, is_owner

DB = Path(__file__).resolve().parents[1] / "sports_predictor.db"


class _Req:
    def __init__(self, host="1.2.3.4"):
        self.client = type("C", (), {"host": host})()
        self.headers = {}


class TestDailyBudget:
    def test_blocks_after_cap(self):
        b = DailyBudget(daily_limit=3)
        for _ in range(3):
            asyncio.run(b(_Req()))
        with pytest.raises(HTTPException) as e:
            asyncio.run(b(_Req()))
        assert e.value.status_code == 429

    def test_cap_is_global_not_per_ip(self):
        """The point: rotating IPs must not mint fresh budget."""
        b = DailyBudget(daily_limit=2)
        asyncio.run(b(_Req(host="1.1.1.1")))
        asyncio.run(b(_Req(host="2.2.2.2")))
        with pytest.raises(HTTPException):
            asyncio.run(b(_Req(host="3.3.3.3")))

    def test_resets_on_utc_day_change(self, monkeypatch):
        b = DailyBudget(daily_limit=1)
        monkeypatch.setattr(DailyBudget, "_today", staticmethod(lambda: "2026-07-26"))
        asyncio.run(b(_Req()))
        with pytest.raises(HTTPException):
            asyncio.run(b(_Req()))
        monkeypatch.setattr(DailyBudget, "_today", staticmethod(lambda: "2026-07-27"))
        asyncio.run(b(_Req()))  # new day, budget restored

    def test_status_reports_remaining(self):
        b = DailyBudget(daily_limit=5)
        asyncio.run(b(_Req()))
        st = b.status()
        assert st["used_today"] == 1 and st["remaining"] == 4


class TestOwnerLabelling:
    def test_no_token_configured_means_nobody_is_owner(self, monkeypatch):
        """Without a configured token, every caller is 'public' — the label
        must never be grantable by guessing."""
        monkeypatch.setattr(security, "ADMIN_TOKEN", "")
        assert not is_owner("anything")
        assert not is_owner(None)

    def test_only_the_exact_token_labels_owner(self, monkeypatch):
        monkeypatch.setattr(security, "ADMIN_TOKEN", "s3cret")
        assert is_owner("s3cret")
        assert not is_owner("guess")
        assert not is_owner(None)


@pytest.mark.skipif(not DB.exists(), reason="needs the local match database")
class TestStoredReliabilityReports:
    def test_reports_exist_for_every_league(self):
        con = sqlite3.connect(DB)
        try:
            leagues = {r[0] for r in con.execute(
                "SELECT league FROM calibration_reports WHERE season='2526'")}
        finally:
            con.close()
        assert {"premier_league", "la_liga", "serie_a",
                "bundesliga", "ligue_1"} <= leagues

    def test_read_path_never_computes(self):
        """stored_report must return instantly (pure SELECT) and untouched
        leagues must yield None rather than triggering a backtest."""
        from app.services.calibration_report import stored_report
        assert asyncio.run(stored_report("premier_league")) is not None
        assert asyncio.run(stored_report("premier_league", season="1899")) is None

    def test_stored_report_declares_its_basis(self):
        """The report must keep saying it is a backtest, not a live record —
        that distinction is the whole credibility story."""
        from app.services.calibration_report import stored_report
        r = asyncio.run(stored_report("premier_league"))
        assert r["basis"] == "walk_forward_backtest"
        assert r["computed_at"]


@pytest.mark.skipif(not DB.exists(), reason="needs the local match database")
class TestPublicSurfaceStaysCheap:
    """Route-level wiring for the public endpoint."""

    @pytest.fixture(scope="class")
    def client(self):
        from fastapi.testclient import TestClient
        import main
        with TestClient(main.app) as c:
            yield c

    def test_reliability_endpoint_is_a_fast_read(self, client):
        import time
        t0 = time.monotonic()
        r = client.get("/api/calibration/reliability?league=premier_league")
        elapsed = time.monotonic() - t0
        assert r.status_code == 200
        # The old implementation replayed a full season here (~3s); a stored
        # read is milliseconds. 500ms is a generous ceiling that still fails
        # loudly if computation ever creeps back into this path.
        assert elapsed < 0.5, f"reliability read took {elapsed:.2f}s — is it computing?"

    def test_missing_report_is_404_not_computation(self, client):
        r = client.get("/api/calibration/reliability?league=premier_league&season=1899")
        assert r.status_code == 404

    def test_unknown_league_is_rejected(self, client):
        r = client.get("/api/calibration/reliability?league=nonsense")
        assert r.status_code == 400
