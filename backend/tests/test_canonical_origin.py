"""Regression tests for header-controlled request URLs.

These reproduce two attacks confirmed against the live deploy before the fix
existed. Both mattered because x402 builds its payment challenge from the
request URL, so whoever controls that string controls the resource a paying
agent is told it is buying:

    Host: evil.example       -> resource https://evil.example/api/v1/...
    X-Forwarded-Proto: http  -> resource http://... on a paid endpoint

The middleware pins scheme and host to PUBLIC_BASE_URL, so neither header can
move them any more.
"""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.security import CanonicalOriginMiddleware, SecurityHeadersMiddleware

BASE = "https://api.example.com"


def _app(pinned: bool = True) -> FastAPI:
    """A bare app that reports the URL it thinks it was reached at.

    That string is the thing under test: it is what x402 embeds in the payment
    challenge as the resource being sold.
    """
    app = FastAPI()

    @app.get("/echo")
    def echo(request: Request):
        return {"url": str(request.url), "scheme": request.url.scheme,
                "host": request.url.netloc}

    if pinned:
        app.add_middleware(CanonicalOriginMiddleware, base_url=BASE)
    return app


class TestCanonicalOrigin:
    def test_host_header_cannot_move_the_url(self):
        c = TestClient(_app())
        body = c.get("/echo", headers={"Host": "evil.example"}).json()
        assert body["host"] == "api.example.com"
        assert "evil.example" not in body["url"]

    def test_forwarded_proto_cannot_downgrade_the_scheme(self):
        c = TestClient(_app())
        body = c.get("/echo", headers={"X-Forwarded-Proto": "http"}).json()
        assert body["scheme"] == "https"

    def test_forwarded_host_cannot_move_the_url(self):
        c = TestClient(_app())
        body = c.get("/echo", headers={"X-Forwarded-Host": "evil.example"}).json()
        assert "evil.example" not in body["url"]

    def test_attack_reproduces_without_the_middleware(self):
        """Guards the guard: if this stops failing, the test app is wrong.

        A regression test that would pass with the fix reverted proves nothing,
        so assert the unprotected app is genuinely vulnerable.
        """
        c = TestClient(_app(pinned=False))
        body = c.get("/echo", headers={"Host": "evil.example"}).json()
        assert body["host"] == "evil.example"

    def test_forwarded_for_is_left_alone(self):
        """The rate limiter identifies clients by this header — do not strip it."""
        app = _app()

        @app.get("/ip")
        def ip(request: Request):
            return {"xff": request.headers.get("x-forwarded-for")}

        c = TestClient(app)
        assert c.get("/ip", headers={"X-Forwarded-For": "1.2.3.4"}).json()["xff"] == "1.2.3.4"

    def test_rejects_a_non_absolute_base_url(self):
        import pytest

        with pytest.raises(ValueError):
            CanonicalOriginMiddleware(None, base_url="api.example.com")


class TestSecurityHeaders:
    def test_headers_are_present(self):
        app = _app()
        app.add_middleware(SecurityHeadersMiddleware, hsts=True)
        r = TestClient(app).get("/echo")
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert "max-age=31536000" in r.headers["strict-transport-security"]

    def test_hsts_is_omitted_when_not_served_over_https(self):
        app = _app()
        app.add_middleware(SecurityHeadersMiddleware, hsts=False)
        r = TestClient(app).get("/echo")
        assert "strict-transport-security" not in r.headers


class TestClientIpSpoofing:
    """The rate limiter keyed on a value the caller controls.

    Reproduced against production: 35 requests with a fixed spoofed
    X-Forwarded-For hit the 30/min limit; 35 with rotating spoofed values
    never hit it, because each forged address opened a fresh bucket.
    """

    def _req(self, headers: dict, client_host: str = "10.0.0.1"):
        from starlette.requests import Request

        raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        return Request({"type": "http", "headers": raw, "client": (client_host, 1234),
                        "method": "GET", "path": "/", "scheme": "https"})

    def test_forged_leading_entry_is_ignored(self, monkeypatch):
        from app.core import security

        monkeypatch.setenv("TRUST_PROXY", "1")
        # What a real proxy produces when the caller sent their own header:
        # the forgery first, the address the proxy observed appended after it.
        ip = security._client_ip(self._req({"x-forwarded-for": "203.0.113.9, 198.51.100.7"}))
        assert ip == "198.51.100.7", "took the caller-supplied entry"

    def test_rotating_forgeries_share_one_bucket(self, monkeypatch):
        from app.core import security

        monkeypatch.setenv("TRUST_PROXY", "1")
        seen = {
            security._client_ip(self._req(
                {"x-forwarded-for": f"203.0.113.{i}, 198.51.100.7"}))
            for i in range(1, 20)
        }
        assert seen == {"198.51.100.7"}, "forged values still open fresh buckets"

    def test_proxy_own_header_wins(self, monkeypatch):
        from app.core import security

        monkeypatch.setenv("TRUST_PROXY", "1")
        monkeypatch.setattr(security, "TRUSTED_CLIENT_IP_HEADER", "fly-client-ip")
        ip = security._client_ip(self._req({
            "fly-client-ip": "198.51.100.7",
            "x-forwarded-for": "203.0.113.9",
        }))
        assert ip == "198.51.100.7"

    def test_proxy_headers_ignored_when_untrusted(self, monkeypatch):
        from app.core import security

        monkeypatch.delenv("TRUST_PROXY", raising=False)
        ip = security._client_ip(self._req(
            {"x-forwarded-for": "203.0.113.9", "fly-client-ip": "203.0.113.10"}))
        assert ip == "10.0.0.1"
