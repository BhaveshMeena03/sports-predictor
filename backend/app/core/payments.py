"""x402: let AI agents pay per request in USDC on Base.

x402 revives HTTP 402 Payment Required. An agent calls the endpoint, gets a 402
describing the price, pays, and retries with a payment header; a facilitator
verifies and settles on-chain. No account, no API key, no invoice.

What is and isn't gated
-----------------------
Everything that is public today STAYS FREE — the track record, reliability
curves, and single-fixture predictions are the evidence, and putting evidence
behind a paywall would defeat the point of publishing it. Only the bulk
slate endpoint (every upcoming fixture, full probability vectors and derived
markets in one response) is priced, because that is the shape an agent wants
and the one that costs us real compute to assemble.

Safety properties
-----------------
* This module never sees a private key. Receiving payment needs only a public
  address; signing happens on the payer's side.
* Payments are OFF unless X402_PAY_TO is set. An unconfigured deploy serves
  the paid route free rather than half-configuring a paywall.
* Testnet by default (Base Sepolia). Real money requires deliberately setting
  X402_NETWORK=mainnet — it is never the fallback.
"""

import logging
import os
import re

log = logging.getLogger(__name__)

# EVM address to receive USDC. Public information — safe in config.
PAY_TO = os.getenv("X402_PAY_TO", "").strip()

# "testnet" (Base Sepolia) or "mainnet" (Base). Default testnet on purpose:
# a typo in this variable must never mean "start taking real money".
NETWORK_NAME = os.getenv("X402_NETWORK", "testnet").strip().lower()

PRICE = os.getenv("X402_PRICE", "$0.02").strip()

_NETWORKS = {
    "testnet": ("eip155:84532", "https://x402.org/facilitator"),
    "mainnet": ("eip155:8453", "https://api.cdp.coinbase.com/platform/v2/x402"),
}

# The one route that costs money. Must match the ASGI path exactly.
PAID_ROUTE = "GET /api/v1/probabilities"

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def is_configured() -> bool:
    return bool(PAY_TO) and _ADDRESS_RE.match(PAY_TO) is not None


def status() -> dict:
    """Public description of the payment setup (no secrets — there are none)."""
    network, facilitator = _NETWORKS.get(NETWORK_NAME, _NETWORKS["testnet"])
    return {
        "enabled": is_configured(),
        "protocol": "x402",
        "network": NETWORK_NAME,
        "chain": network,
        "facilitator": facilitator,
        "paid_route": PAID_ROUTE,
        "price": PRICE,
        "pay_to": PAY_TO or None,
        "note": ("Free endpoints stay free: the track record, reliability "
                 "curves and single-fixture predictions are public evidence. "
                 "Only the bulk slate is priced."),
    }


def install(app) -> bool:
    """Attach the x402 middleware. Returns True if payments are active.

    No-ops (loudly) when unconfigured so the app still boots and serves the
    route for free — a missing address should degrade to 'free', never to a
    broken paywall that takes money nobody can collect.
    """
    if not PAY_TO:
        log.info("x402 disabled: X402_PAY_TO not set — /v1/probabilities is free.")
        return False
    if not _ADDRESS_RE.match(PAY_TO):
        log.error("x402 disabled: X402_PAY_TO=%r is not a valid 0x EVM address.", PAY_TO)
        return False
    if NETWORK_NAME not in _NETWORKS:
        log.error("x402 disabled: X402_NETWORK=%r must be 'testnet' or 'mainnet'.",
                  NETWORK_NAME)
        return False

    try:
        from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
        from x402.http.middleware.fastapi import PaymentMiddlewareASGI
        from x402.http.types import RouteConfig
        from x402.mechanisms.evm.exact import ExactEvmServerScheme
        from x402.server import x402ResourceServer
    except ImportError as e:
        log.error("x402 disabled: SDK not installed (%s). pip install 'x402[evm,fastapi]'", e)
        return False

    network, facilitator_url = _NETWORKS[NETWORK_NAME]
    server = x402ResourceServer(
        HTTPFacilitatorClient(FacilitatorConfig(url=facilitator_url))
    )
    server.register(network, ExactEvmServerScheme())

    routes = {
        PAID_ROUTE: RouteConfig(
            accepts=[PaymentOption(
                scheme="exact",
                pay_to=PAY_TO,
                price=PRICE,
                network=network,
            )],
            mime_type="application/json",
            description=(
                "Calibrated 1X2 probabilities plus derived markets (totals, "
                "BTTS, correct score) for every upcoming fixture across the "
                "big-five European leagues. Backed by a public, Brier-scored "
                "track record."
            ),
            service_name="Sports Predictor",
            tags=["sports", "football", "probabilities", "predictions"],
        )
    }

    app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)
    log.info("x402 ENABLED on %s (%s) — %s at %s", NETWORK_NAME, network,
             PAID_ROUTE, PRICE)
    return True
