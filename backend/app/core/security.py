"""Authentication, rate limiting, and CORS policy.

Threat model this is written against: the API is internet-reachable and some
endpoints spend real money (Anthropic credits via the LLM analyst) or mutate
state (bets, ensemble weights, Elo rebuilds). Without a gate, an unauthenticated
caller can drain the API budget or wipe the bet ledger.

Two independent controls:

  * `require_admin` — a shared-secret header on everything mutating or
    expensive. Fails CLOSED: with no ADMIN_TOKEN configured, those endpoints
    are reachable only from loopback, so a misconfigured deploy is locked down
    rather than wide open.
  * `RateLimiter` — per-IP token buckets, plus a process-wide ceiling that caps
    total spend no matter how many distinct IPs show up.
"""

import ipaddress
import logging
import os
import time
from collections import deque
from hmac import compare_digest

from fastapi import Header, HTTPException, Request

log = logging.getLogger(__name__)

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

# Requests/minute per client IP on public endpoints.
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT_RPM", "30"))
# Requests/minute across ALL clients for endpoints that call a paid model.
# This is the actual spend ceiling — per-IP limits alone don't bound cost.
LLM_GLOBAL_RPM = int(os.getenv("LLM_GLOBAL_RPM", "20"))
LLM_RPM = int(os.getenv("LLM_RPM", "5"))


# A header the edge proxy sets itself and overwrites on the way in, so a client
# cannot forge it. "fly-client-ip" on Fly; set to "" to disable, or to the
# equivalent header on another host.
TRUSTED_CLIENT_IP_HEADER = os.getenv(
    "TRUSTED_CLIENT_IP_HEADER", "fly-client-ip").strip().lower()


def _client_ip(request: Request) -> str:
    """Best-effort client IP, resistant to a forged X-Forwarded-For.

    Only consults proxy headers when TRUST_PROXY=1, because they are
    attacker-controlled otherwise — a spoofed value would hand every request a
    fresh rate-limit bucket and defeat the limiter entirely.

    Trusting the header was not sufficient on its own. Proxies APPEND to
    X-Forwarded-For, so a caller who sends one of their own lands first in the
    list and the real address is appended after it. Reading entry [0] — which
    this did — let the caller pick their own bucket key. Measured against
    production before the fix: 35 requests carrying a fixed spoofed value hit
    the limit at exactly 30, while 35 requests each carrying a different
    spoofed value never hit it at all.

    So: prefer the proxy's own header, and otherwise take the RIGHTMOST
    X-Forwarded-For entry — appended by the nearest trusted proxy, and the one
    entry a client cannot write past.

    The global RPM ceiling and the daily budget were never affected by this.
    They do not key on identity, which is exactly why they are the real cap on
    spend rather than a nicety.
    """
    if os.getenv("TRUST_PROXY") == "1":
        if TRUSTED_CLIENT_IP_HEADER:
            direct = request.headers.get(TRUSTED_CLIENT_IP_HEADER, "").strip()
            if direct:
                return direct
        fwd = request.headers.get("x-forwarded-for", "")
        hops = [h.strip() for h in fwd.split(",") if h.strip()]
        if hops:
            return hops[-1]
    return request.client.host if request.client else "unknown"


def _is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


async def require_admin(request: Request,
                        x_admin_token: str | None = Header(default=None)) -> None:
    """Gate for mutating / paid endpoints.

    With ADMIN_TOKEN set, the header must match it. With no token configured,
    access is restricted to loopback — so forgetting to set it in production
    yields a locked door, not an open one.
    """
    if not ADMIN_TOKEN:
        if _is_loopback(_client_ip(request)):
            return
        log.error("Admin endpoint hit from %s with no ADMIN_TOKEN configured "
                  "— refusing. Set ADMIN_TOKEN to enable remote admin access.",
                  _client_ip(request))
        raise HTTPException(
            status_code=503,
            detail="Admin endpoints are disabled: server has no ADMIN_TOKEN configured.",
        )
    if not x_admin_token or not compare_digest(x_admin_token, ADMIN_TOKEN):
        # compare_digest, not ==, so a wrong token can't be recovered by timing.
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token.")


def is_owner(x_admin_token: str | None) -> bool:
    """Non-raising check: does this request carry the admin token?

    Used to LABEL rather than gate — e.g. tagging prediction-log rows as
    'owner' vs 'public' so anonymous traffic can't pollute the owner's
    calibration tracker. Never use this to protect an endpoint; that's
    require_admin's job, which fails closed.
    """
    return bool(ADMIN_TOKEN) and bool(x_admin_token) \
        and compare_digest(x_admin_token, ADMIN_TOKEN)


class RateLimiter:
    """Sliding-window limiter.

    In-process only, which is the right scope for a single-replica deploy. Behind
    multiple replicas this becomes per-replica; move the buckets to Redis before
    scaling out rather than assuming this still bounds anything.
    """

    def __init__(self, rpm: int, global_rpm: int | None = None, name: str = ""):
        self.rpm = rpm
        self.global_rpm = global_rpm
        self.name = name
        self._per_ip: dict[str, deque[float]] = {}
        self._global: deque[float] = deque()

    @staticmethod
    def _trim(bucket: deque[float], now: float) -> None:
        cutoff = now - 60.0
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

    def _sweep(self, now: float) -> None:
        # Drop idle buckets so a stream of unique IPs can't grow this forever.
        if len(self._per_ip) > 10_000:
            for ip in [ip for ip, b in self._per_ip.items()
                       if not b or b[-1] < now - 300]:
                self._per_ip.pop(ip, None)

    async def __call__(self, request: Request) -> None:
        now = time.monotonic()
        ip = _client_ip(request)

        if self.global_rpm is not None:
            self._trim(self._global, now)
            if len(self._global) >= self.global_rpm:
                log.warning("global rate limit hit on %s", self.name or "api")
                raise HTTPException(
                    status_code=429,
                    detail="Service is at capacity, please retry shortly.",
                    headers={"Retry-After": "60"},
                )

        bucket = self._per_ip.setdefault(ip, deque())
        self._trim(bucket, now)
        if len(bucket) >= self.rpm:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded, please slow down.",
                headers={"Retry-After": "60"},
            )

        bucket.append(now)
        if self.global_rpm is not None:
            self._global.append(now)
        self._sweep(now)


class DailyBudget:
    """Absolute daily call ceiling for endpoints that spend real money.

    Per-minute limits bound burst rate, not the bill: 20/min globally still
    compounds to 28,800 calls/day. This is the kill-switch — once the day's
    budget is gone, paid endpoints answer 429 until UTC midnight regardless of
    who is asking. In-process, same single-replica scope as RateLimiter.
    """

    def __init__(self, daily_limit: int, name: str = ""):
        self.daily_limit = daily_limit
        self.name = name
        self._day: str | None = None
        self._used = 0

    @staticmethod
    def _today() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _roll(self) -> None:
        today = self._today()
        if today != self._day:
            self._day, self._used = today, 0

    def status(self) -> dict:
        self._roll()
        return {"daily_limit": self.daily_limit, "used_today": self._used,
                "remaining": max(0, self.daily_limit - self._used)}

    async def __call__(self, request: Request) -> None:
        self._roll()
        if self._used >= self.daily_limit:
            log.error("daily budget exhausted on %s (%d calls) — refusing "
                      "paid requests until UTC midnight", self.name, self._used)
            raise HTTPException(
                status_code=429,
                detail="Daily capacity reached; resets at 00:00 UTC.",
                headers={"Retry-After": "3600"},
            )
        self._used += 1


class PerClientDailyBudget:
    """A daily ceiling on model-backed requests from a SINGLE client.

    llm_daily_budget bounds what the service spends; nothing bounded what one
    caller could spend of it. The per-minute limiter does not help -- it caps
    rate, not total -- so a patient script sitting exactly on 5/min drains the
    whole 300/day budget in an hour, every request legal on the way, and every
    genuine visitor is refused until UTC midnight.

    The cap is far above human behaviour: a person exploring the analyzer might
    make ten or twenty calls in a day, so only automation should ever meet it.
    Draining the service now needs a spread of addresses rather than one, and
    the log line names the client when a cap is hit.

    In-memory and per-process, matching the other limiters. With a second
    replica this needs a shared store, or each replica grants its own
    allowance.
    """

    MAX_CLIENTS = 20_000  # bounded: the key is attacker-controlled

    def __init__(self, limit: int | None = None, name: str = ""):
        self._limit = limit if limit is not None else int(
            os.getenv("LLM_PER_CLIENT_DAILY_CAP", "40"))
        self._name = name
        self._day: str | None = None
        self._counts: dict[str, int] = {}
        self._seen: dict[str, float] = {}

    def _roll(self) -> None:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self._day:
            self._day, self._counts, self._seen = today, {}, {}

    async def __call__(self, request: Request) -> None:
        if self._limit <= 0:      # 0 disables
            return
        self._roll()
        key = _client_ip(request)
        used = self._counts.get(key, 0)
        if used >= self._limit:
            log.warning("%s per-client daily cap reached: %s used %d — refusing.",
                        self._name, key, used)
            raise HTTPException(
                status_code=429,
                detail=("You've reached today's limit for this endpoint. "
                        "It resets at midnight UTC."),
                headers={"Retry-After": "3600"},
            )
        if len(self._counts) >= self.MAX_CLIENTS and key not in self._counts:
            oldest = min(self._seen, key=self._seen.get)
            self._counts.pop(oldest, None)
            self._seen.pop(oldest, None)
        self._counts[key] = used + 1
        self._seen[key] = time.monotonic()

    def state(self) -> dict:
        self._roll()
        busiest = max(self._counts.values(), default=0)
        return {"day": self._day, "clients": len(self._counts),
                "limit": self._limit, "busiest_client": busiest}


# Public reads/analysis.
public_limit = RateLimiter(RATE_LIMIT_RPM, name="public")
# Endpoints that call a paid model — per-IP AND a hard global ceiling.
llm_limit = RateLimiter(LLM_RPM, global_rpm=LLM_GLOBAL_RPM, name="llm")
# ...and an absolute per-day cap on top. ~300 calls/day at ~1-2c each bounds
# the worst case to a few dollars, not a drained account.
llm_daily_budget = DailyBudget(int(os.getenv("LLM_DAILY_CAP", "300")), name="llm")
# ...and a per-caller slice of it, so one client cannot take the whole day.
llm_per_client = PerClientDailyBudget(name="llm")


def cors_origins() -> list[str]:
    """Explicit allowlist from CORS_ORIGINS (comma-separated).

    Never returns "*": the previous config paired a wildcard with
    allow_credentials=True, which browsers reject outright and which signals an
    intent to let any site call this API with the user's cookies attached.
    """
    raw = os.getenv("CORS_ORIGINS", "")
    origins = [o.strip() for o in raw.split(",") if o.strip() and o.strip() != "*"]
    return origins or ["http://localhost:3000", "http://127.0.0.1:3000"]


# The canonical public origin, e.g. "https://sports-predictor-api.fly.dev".
# When set, every request is treated as having arrived there regardless of what
# the caller's headers claim. See CanonicalOriginMiddleware for why.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")


class CanonicalOriginMiddleware:
    """Pin the request's scheme and host to the configured public origin.

    Why this is not paranoia. FastAPI builds absolute URLs from the request
    scheme and the Host header, and x402 puts that URL into the payment
    challenge as the resource being sold. Both inputs are attacker-controlled
    on a deploy that trusts proxy headers, which ours must because Fly
    terminates TLS. Verified against production before this existed:

        Host: evil.example       -> resource https://evil.example/api/v1/...
        X-Forwarded-Proto: http  -> resource http://sports-predictor-api...

    The first hands a caller a challenge naming a resource we do not serve;
    the second advertises a plaintext URL for an endpoint that takes payment.
    Neither steals funds directly, but the resource URL is what a paying agent
    follows and what Coinbase records when a payment settles, so letting a
    stranger choose it is not something to leave running.

    Rewriting rather than rejecting: platform health checks reach the machine
    by an internal name, and a strict Host allowlist would fail them. Pinning
    normalises those instead of 400ing, and makes the advertised resource URL
    deterministic — the same string for every caller, which is also what the
    Bazaar listing needs.

    X-Forwarded-For is deliberately left alone: the rate limiter reads it to
    identify clients, and it is gated separately by TRUST_PROXY.
    """

    _STRIP = (b"x-forwarded-proto", b"x-forwarded-host", b"x-forwarded-scheme")

    def __init__(self, app, base_url: str):
        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(
                f"PUBLIC_BASE_URL must be an absolute URL like "
                f"https://api.example.com — got {base_url!r}"
            )
        self.app = app
        self.scheme = parsed.scheme
        self.host = parsed.netloc.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope["scheme"] = self.scheme
            headers = [(k, v) for k, v in scope["headers"]
                       if k.lower() not in self._STRIP and k.lower() != b"host"]
            headers.append((b"host", self.host))
            scope["headers"] = headers
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    """Response headers that cost nothing and close off whole bug classes.

    nosniff stops a browser second-guessing our JSON content type; DENY stops
    the API being framed; HSTS keeps a browser from ever retrying over http.
    No CSP: this process serves JSON, not pages, and a CSP here would be
    decoration.
    """

    def __init__(self, app, hsts: bool = True):
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {k.lower() for k, _ in headers}
                extra = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                ]
                if self.hsts:
                    extra.append((b"strict-transport-security",
                                  b"max-age=31536000; includeSubDomains"))
                headers.extend((k, v) for k, v in extra if k not in present)
            await send(message)

        await self.app(scope, receive, send_wrapper)
