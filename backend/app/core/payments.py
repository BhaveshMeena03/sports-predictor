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
* Mainnet additionally requires CDP credentials. Coinbase's facilitator
  rejects unauthenticated calls (401), so without them the middleware would
  answer 402 and then fail every verification — a paywall that collects
  nothing. Mainnet refuses to enable rather than doing that.
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

# Coinbase Developer Platform key, required only on mainnet: the testnet
# facilitator is open, the mainnet one answers 401 without these.
CDP_KEY_ID = os.getenv("CDP_API_KEY_ID", "").strip()
CDP_KEY_SECRET = os.getenv("CDP_API_KEY_SECRET", "").strip()

_NETWORKS = {
    "testnet": ("eip155:84532", "https://x402.org/facilitator"),
    "mainnet": ("eip155:8453", "https://api.cdp.coinbase.com/platform/v2/x402"),
}

# The one route that costs money. Must match the ASGI path exactly.
PAID_ROUTE = "GET /api/v1/probabilities"

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _mainnet_ready() -> bool:
    """Mainnet needs CDP credentials; testnet's facilitator is open.

    Coinbase answers 401 unauthenticated, so without keys the paywall would
    return 402 and then fail every settlement.
    """
    return NETWORK_NAME != "mainnet" or bool(CDP_KEY_ID and CDP_KEY_SECRET)


def is_configured() -> bool:
    return (bool(PAY_TO)
            and _ADDRESS_RE.match(PAY_TO) is not None
            and NETWORK_NAME in _NETWORKS
            and _mainnet_ready())


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


def _discovery_extension(declare, OutputConfig) -> dict:
    """Bazaar declaration: input schema, output schema, and a real example.

    Takes its constructors as arguments so this module still imports when the
    x402 SDK isn't installed (tests, or a deploy that skipped the extra).
    """
    ext = declare(
        input={"days": 14},
        input_schema={
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer", "minimum": 1, "maximum": 30, "default": 14,
                    "description": "How far ahead to include fixtures, in days.",
                }
            },
            "additionalProperties": False,
        },
        output=OutputConfig(
            schema={
                "type": "object",
                "required": ["generated_at", "count", "fixtures"],
                "properties": {
                    "generated_at": {"type": "string", "format": "date-time"},
                    "window_days": {"type": "integer"},
                    "count": {"type": "integer"},
                    "fixtures": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["date", "home", "away", "probabilities"],
                            "properties": {
                                "league": {"type": "string"},
                                "league_label": {"type": "string"},
                                "date": {"type": "string", "format": "date"},
                                "home": {"type": "string"},
                                "away": {"type": "string"},
                                "probabilities": {
                                    "type": "object",
                                    "description": ("Calibrated 1X2 probabilities; "
                                                    "sum to 1."),
                                    "properties": {
                                        "home": {"type": "number"},
                                        "draw": {"type": "number"},
                                        "away": {"type": "number"},
                                    },
                                },
                                "expected_goals": {
                                    "type": "object",
                                    "properties": {
                                        "home": {"type": "number"},
                                        "away": {"type": "number"},
                                    },
                                },
                                "markets": {
                                    "type": "object",
                                    "description": ("Over/under lines, both-teams-"
                                                    "to-score and likeliest "
                                                    "scorelines. Raw model output, "
                                                    "not calibrated."),
                                },
                            },
                        },
                    },
                },
            },
            example={
                "generated_at": "2026-08-14T09:00:00+00:00",
                "window_days": 14,
                "count": 1,
                "fixtures": [{
                    "league": "premier_league",
                    "league_label": "Premier League",
                    "date": "2026-08-21",
                    "home": "Arsenal", "away": "Coventry City",
                    "probabilities": {"home": 0.66, "draw": 0.20, "away": 0.14},
                    "expected_goals": {"home": 2.14, "away": 0.71},
                    "markets": {
                        "totals": {"2.5": {"over": 0.54, "under": 0.46}},
                        "btts": {"yes": 0.45, "no": 0.55},
                        "correct_score": [{"score": "2-0", "p": 0.13}],
                    },
                }],
            },
        ),
    )
    # The SDK validates the declaration at startup but only injects `method`
    # at request time, so it warns about its own not-yet-enriched value.
    # This route is always GET, so stating it up front is accurate and keeps
    # the boot logs clean; the runtime enrichment sets the same value.
    info = ext["bazaar"]["info"]
    info["input"]["method"] = "GET"
    schema_props = ext["bazaar"].get("schema", {}).get("properties", {})
    inp_schema = schema_props.get("input", {})
    if isinstance(inp_schema, dict):
        inp_schema.setdefault("required", [])
        if "method" not in inp_schema["required"]:
            inp_schema["required"].append("method")
        inp_schema.setdefault("properties", {})["method"] = {
            "type": "string", "const": "GET",
        }
    return ext


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
    if not _mainnet_ready():
        log.error("x402 disabled: mainnet needs CDP_API_KEY_ID and "
                  "CDP_API_KEY_SECRET — Coinbase's facilitator rejects "
                  "unauthenticated requests. Staying free.")
        return False

    try:
        from x402.extensions.bazaar import (
            OutputConfig,
            bazaar_resource_server_extension,
            declare_discovery_extension,
        )
        from x402.http import (
            CreateHeadersAuthProvider,
            FacilitatorConfig,
            HTTPFacilitatorClient,
            PaymentOption,
        )
        from x402.http.middleware.fastapi import PaymentMiddlewareASGI
        from x402.http.types import RouteConfig
        from x402.mechanisms.evm.exact import ExactEvmServerScheme
        from x402.server import x402ResourceServer
    except ImportError as e:
        log.error("x402 disabled: SDK not installed (%s). pip install 'x402[evm,fastapi]'", e)
        return False

    network, facilitator_url = _NETWORKS[NETWORK_NAME]

    # Testnet's facilitator is open; mainnet's is Coinbase CDP and needs
    # signed headers on every call. The SDK takes a create_headers callable
    # in CDP's own shape, so the credentials never leave this process.
    cfg = FacilitatorConfig(url=facilitator_url)
    if NETWORK_NAME == "mainnet":
        try:
            from cdp.auth.utils.http import GetAuthHeadersOptions, get_auth_headers
        except ImportError as e:
            log.error("x402 disabled: mainnet needs the cdp-sdk for facilitator "
                      "auth (%s). pip install cdp-sdk", e)
            return False

        def _cdp_headers() -> dict:
            # Re-signed per call: CDP's JWTs are short-lived, so caching them
            # would start failing quietly a couple of minutes in.
            def sign(path: str) -> dict:
                return get_auth_headers(GetAuthHeadersOptions(
                    api_key_id=CDP_KEY_ID,
                    api_key_secret=CDP_KEY_SECRET,
                    request_method="POST",
                    request_host="api.cdp.coinbase.com",
                    request_path=f"/platform/v2/x402/{path}",
                ))
            return {"verify": sign("verify"), "settle": sign("settle"),
                    "supported": sign("supported"), "bazaar": sign("discovery/resources")}

        cfg = FacilitatorConfig(
            url=facilitator_url,
            auth_provider=CreateHeadersAuthProvider(_cdp_headers),
        )

    server = x402ResourceServer(HTTPFacilitatorClient(cfg))
    server.register(network, ExactEvmServerScheme())
    # Injects the HTTP method into the discovery extension at request time —
    # without it the Bazaar declaration is incomplete and may not index.
    server.register_extension(bazaar_resource_server_extension)

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
            # Bazaar discovery: tells agents how to CALL this endpoint and what
            # comes back. Coinbase's catalog indexes on a real settlement, and
            # semantic search ranks on this metadata — a bare URL with no
            # schema is findable in theory and useless in practice.
            extensions=_discovery_extension(
                declare_discovery_extension, OutputConfig),
        )
    }

    app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)
    log.info("x402 ENABLED on %s (%s) — %s at %s", NETWORK_NAME, network,
             PAID_ROUTE, PRICE)
    return True
