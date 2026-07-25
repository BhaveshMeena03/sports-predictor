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


def _client_ip(request: Request) -> str:
    """Best-effort client IP.

    Only trusts X-Forwarded-For when TRUST_PROXY=1, because the header is
    attacker-controlled otherwise — a spoofed value would hand every request a
    fresh rate-limit bucket and defeat the limiter entirely.
    """
    if os.getenv("TRUST_PROXY") == "1":
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
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


# Public reads/analysis.
public_limit = RateLimiter(RATE_LIMIT_RPM, name="public")
# Endpoints that call a paid model — per-IP AND a hard global ceiling.
llm_limit = RateLimiter(LLM_RPM, global_rpm=LLM_GLOBAL_RPM, name="llm")


def cors_origins() -> list[str]:
    """Explicit allowlist from CORS_ORIGINS (comma-separated).

    Never returns "*": the previous config paired a wildcard with
    allow_credentials=True, which browsers reject outright and which signals an
    intent to let any site call this API with the user's cookies attached.
    """
    raw = os.getenv("CORS_ORIGINS", "")
    origins = [o.strip() for o in raw.split(",") if o.strip() and o.strip() != "*"]
    return origins or ["http://localhost:3000", "http://127.0.0.1:3000"]
