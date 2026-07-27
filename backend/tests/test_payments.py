"""x402 payment gating.

Two properties matter more than the happy path:

1. An unconfigured deploy must serve the slate FREE, not half-paywalled. A
   missing address means "payments off", never "collect to nowhere".
2. Testnet is the default. A typo in X402_NETWORK must never resolve to
   mainnet and start moving real money.
"""

import importlib

import pytest


def reload_payments(monkeypatch, **env):
    for k in ("X402_PAY_TO", "X402_NETWORK", "X402_PRICE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import app.core.payments as p
    return importlib.reload(p)


class TestConfiguration:
    def test_disabled_without_address(self, monkeypatch):
        p = reload_payments(monkeypatch)
        assert p.is_configured() is False
        assert p.status()["enabled"] is False

    def test_rejects_malformed_address(self, monkeypatch):
        """A typo'd address must disable payments, not accept funds nowhere."""
        for bad in ("not-an-address", "0x123", "0xZZZZ", "0x" + "0" * 39):
            p = reload_payments(monkeypatch, X402_PAY_TO=bad)
            assert p.is_configured() is False, bad

    def test_accepts_valid_address(self, monkeypatch):
        p = reload_payments(monkeypatch, X402_PAY_TO="0x" + "a" * 40)
        assert p.is_configured() is True

    def test_defaults_to_testnet(self, monkeypatch):
        p = reload_payments(monkeypatch, X402_PAY_TO="0x" + "a" * 40)
        s = p.status()
        assert s["network"] == "testnet"
        assert s["chain"] == "eip155:84532"

    def test_mainnet_requires_explicit_opt_in(self, monkeypatch):
        p = reload_payments(monkeypatch, X402_PAY_TO="0x" + "a" * 40,
                            X402_NETWORK="mainnet")
        assert p.status()["chain"] == "eip155:8453"

    def test_unknown_network_does_not_silently_become_mainnet(self, monkeypatch):
        p = reload_payments(monkeypatch, X402_PAY_TO="0x" + "a" * 40,
                            X402_NETWORK="typo")
        assert p.status()["chain"] == "eip155:84532"
        assert p.install(object()) is False   # refuses to install at all

    def test_status_exposes_no_secret(self, monkeypatch):
        """Receiving needs only a public address — there is no key here, and
        this test exists so nobody adds one later."""
        p = reload_payments(monkeypatch, X402_PAY_TO="0x" + "a" * 40)
        blob = repr(p.status()).lower()
        for leak in ("private", "secret", "mnemonic", "seed", "key"):
            assert leak not in blob


class TestGating:
    def test_slate_is_free_when_unconfigured(self, monkeypatch):
        from fastapi.testclient import TestClient
        reload_payments(monkeypatch)
        import main
        importlib.reload(main)
        with TestClient(main.app) as c:
            assert c.get("/api/v1/probabilities?days=1").status_code == 200

    @pytest.mark.parametrize("path", ["/api/health", "/api/trackrecord"])
    def test_public_evidence_never_gated(self, monkeypatch, path):
        """The track record is the product's credibility — it stays free even
        with payments switched on."""
        from fastapi.testclient import TestClient
        reload_payments(monkeypatch, X402_PAY_TO="0x" + "a" * 40)
        import main
        importlib.reload(main)
        with TestClient(main.app) as c:
            assert c.get(path).status_code == 200
