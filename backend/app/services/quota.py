"""
Tiny in-process quota tracker for external sports APIs.

- API-Football free tier: 100 requests/day  (resets at UTC midnight)
- The Odds API free tier:  500 requests/month (we trust the x-requests-remaining
  header the API itself returns, so this module is informational for it)

Calls are counted in-memory; restart resets the counter. Good enough for
single-user/personal use.
"""
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


class QuotaTracker:
    def __init__(self, name: str, daily_limit: int = 0, monthly_limit: int = 0):
        self.name = name
        self.daily_limit = daily_limit
        self.monthly_limit = monthly_limit
        self._daily_count = 0
        self._monthly_count = 0
        self._day_anchor = datetime.now(timezone.utc).date()
        self._month_anchor = datetime.now(timezone.utc).strftime("%Y-%m")
        # Last header echo from the provider (Odds API returns one)
        self.last_remaining: int | None = None

    def _maybe_reset(self):
        now = datetime.now(timezone.utc)
        if now.date() != self._day_anchor:
            self._daily_count = 0
            self._day_anchor = now.date()
        if now.strftime("%Y-%m") != self._month_anchor:
            self._monthly_count = 0
            self._month_anchor = now.strftime("%Y-%m")

    def can_call(self) -> bool:
        """True if we still have budget on both daily and monthly windows."""
        self._maybe_reset()
        if self.daily_limit and self._daily_count >= self.daily_limit:
            log.warning("%s: daily quota exhausted (%d/%d)", self.name, self._daily_count, self.daily_limit)
            return False
        if self.monthly_limit and self._monthly_count >= self.monthly_limit:
            log.warning("%s: monthly quota exhausted (%d/%d)", self.name, self._monthly_count, self.monthly_limit)
            return False
        return True

    def record(self, headers_remaining: int | None = None):
        self._maybe_reset()
        self._daily_count += 1
        self._monthly_count += 1
        if headers_remaining is not None:
            self.last_remaining = headers_remaining

    def status(self) -> dict:
        self._maybe_reset()
        return {
            "name": self.name,
            "used_today": self._daily_count,
            "daily_limit": self.daily_limit or None,
            "used_this_month": self._monthly_count,
            "monthly_limit": self.monthly_limit or None,
            "provider_reported_remaining": self.last_remaining,
        }


api_football_quota = QuotaTracker("api-football", daily_limit=100)
odds_api_quota = QuotaTracker("odds-api", monthly_limit=500)
