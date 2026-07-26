"""Auth, rate limiting, and CORS policy.

These endpoints spend money (the LLM analyst) and mutate state (bet ledger,
ensemble weights, Elo rebuilds). Before this pass the API had no auth, no rate
limit, and CORS set to "*" with credentials allowed — so any caller could drain
the model budget or wipe the ledger. Each test below pins one of those holes
shut.
"""

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core import security
from app.core.security import RateLimiter, cors_origins


class _Req:
    """Minimal stand-in for a Starlette Request (client host + headers)."""

    def __init__(self, host="1.2.3.4", headers=None):
        self.client = type("C", (), {"host": host})()
        self.headers = headers or {}


def call(limiter, req):
    asyncio.run(limiter(req))


class TestAdminAuth:
    def test_fails_closed_without_token_from_remote(self, monkeypatch):
        """No ADMIN_TOKEN configured must NOT mean 'open to everyone'."""
        monkeypatch.setattr(security, "ADMIN_TOKEN", "")
        with pytest.raises(HTTPException) as e:
            asyncio.run(security.require_admin(_Req(host="8.8.8.8"), None))
        assert e.value.status_code == 503

    def test_loopback_allowed_without_token(self, monkeypatch):
        """Local development stays frictionless."""
        monkeypatch.setattr(security, "ADMIN_TOKEN", "")
        asyncio.run(security.require_admin(_Req(host="127.0.0.1"), None))

    def test_correct_token_accepted(self, monkeypatch):
        monkeypatch.setattr(security, "ADMIN_TOKEN", "s3cret")
        asyncio.run(security.require_admin(_Req(host="8.8.8.8"), "s3cret"))

    def test_wrong_token_rejected(self, monkeypatch):
        monkeypatch.setattr(security, "ADMIN_TOKEN", "s3cret")
        with pytest.raises(HTTPException) as e:
            asyncio.run(security.require_admin(_Req(host="8.8.8.8"), "guess"))
        assert e.value.status_code == 401

    def test_token_required_even_from_loopback_when_configured(self, monkeypatch):
        """Once a token exists it is enforced everywhere — loopback is only a
        fallback for the unconfigured case, not a permanent bypass."""
        monkeypatch.setattr(security, "ADMIN_TOKEN", "s3cret")
        with pytest.raises(HTTPException):
            asyncio.run(security.require_admin(_Req(host="127.0.0.1"), None))


class TestRateLimiting:
    def test_blocks_past_the_per_ip_limit(self):
        rl = RateLimiter(rpm=3)
        for _ in range(3):
            call(rl, _Req())
        with pytest.raises(HTTPException) as e:
            call(rl, _Req())
        assert e.value.status_code == 429
        assert e.value.headers["Retry-After"] == "60"

    def test_limits_are_per_ip(self):
        rl = RateLimiter(rpm=1)
        call(rl, _Req(host="1.1.1.1"))
        call(rl, _Req(host="2.2.2.2"))          # different client, own budget
        with pytest.raises(HTTPException):
            call(rl, _Req(host="1.1.1.1"))

    def test_global_ceiling_bounds_total_spend(self):
        """The point of the global cap: per-IP limits alone don't bound cost
        when an attacker rotates IPs, and every one of these calls bills us."""
        rl = RateLimiter(rpm=100, global_rpm=2)
        call(rl, _Req(host="1.1.1.1"))
        call(rl, _Req(host="2.2.2.2"))
        with pytest.raises(HTTPException) as e:
            call(rl, _Req(host="3.3.3.3"))      # fresh IP, still refused
        assert e.value.status_code == 429


class TestProxyHeaderTrust:
    def test_forwarded_header_ignored_by_default(self, monkeypatch):
        """X-Forwarded-For is caller-controlled. Honouring it unconditionally
        would let one client mint unlimited rate-limit buckets."""
        monkeypatch.delenv("TRUST_PROXY", raising=False)
        rl = RateLimiter(rpm=1)
        call(rl, _Req(host="9.9.9.9", headers={"x-forwarded-for": "1.1.1.1"}))
        with pytest.raises(HTTPException):
            call(rl, _Req(host="9.9.9.9", headers={"x-forwarded-for": "2.2.2.2"}))

    def test_forwarded_header_used_when_explicitly_trusted(self, monkeypatch):
        monkeypatch.setenv("TRUST_PROXY", "1")
        rl = RateLimiter(rpm=1)
        call(rl, _Req(host="9.9.9.9", headers={"x-forwarded-for": "1.1.1.1"}))
        call(rl, _Req(host="9.9.9.9", headers={"x-forwarded-for": "2.2.2.2"}))


class TestCORS:
    def test_never_wildcard(self, monkeypatch):
        """allow_origins=["*"] with allow_credentials=True is the bug this
        replaced: browsers reject it, and it advertises intent to let any site
        call the API with the visitor's credentials."""
        monkeypatch.setenv("CORS_ORIGINS", "*")
        assert "*" not in cors_origins()

    def test_reads_explicit_allowlist(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example")
        assert cors_origins() == ["https://a.example", "https://b.example"]

    def test_defaults_to_localhost(self, monkeypatch):
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        assert all("localhost" in o or "127.0.0.1" in o for o in cors_origins())


class TestRoutesAreGuarded:
    """Endpoint-level wiring: the dependency has to actually be attached."""

    @pytest.fixture(scope="class")
    def client(self):
        import main
        with TestClient(main.app) as c:
            yield c

    @pytest.mark.parametrize("method,path", [
        ("post", "/api/scheduler/run-now"),
        ("post", "/api/historical/download"),
        ("post", "/api/backtest"),
        ("post", "/api/clubs/daily-update"),
        ("delete", "/api/bets"),
        # Personal financial data — the bets ledger and everything derived
        # from it (P/L, stakes, ROI) is the owner's, not the public's.
        ("get", "/api/bets"),
        ("get", "/api/bets/summary"),
        ("get", "/api/calibration"),
        ("get", "/api/dashboard"),
    ])
    def test_admin_endpoints_refuse_anonymous_remote_callers(self, client, method, path):
        r = getattr(client, method)(path)
        assert r.status_code in (401, 503), f"{path} answered {r.status_code} unauthenticated"

    def test_public_read_still_works(self, client):
        assert client.get("/api/health").status_code == 200
