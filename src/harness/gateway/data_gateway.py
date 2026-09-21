"""Rate-limited data gateway with access scoring."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional, Set

from harness.core.exceptions import (
    BudgetExceededError,
    RateLimitExceededError,
    SourceBlockedError,
    SourceNotFoundError,
)
from harness.gateway.scoring import AccessScore, compute_access_score


class _TokenBucket:
    """Per-source token bucket for rate limiting."""

    def __init__(self, rate: float, capacity: int):
        self.rate = rate  # tokens per second
        self.capacity = capacity
        self.tokens = float(capacity)
        self.last_update = time.monotonic()

    def consume(self, tokens: int = 1) -> bool:
        """Attempt to consume tokens from the bucket."""
        now = time.monotonic()
        elapsed = now - self.last_update
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_update = now
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    def refill(self, tokens: Optional[int] = None) -> None:
        """Manually refill tokens."""
        if tokens is None:
            self.tokens = float(self.capacity)
        else:
            self.tokens = min(self.capacity, self.tokens + tokens)


class DataGateway:
    """Rate-limited data gateway with access scoring and budget tracking."""

    def __init__(
        self,
        rate_limit_rps: float = 10.0,
        daily_budget_usd: float = 100.0,
    ):
        self.rate_limit_rps = rate_limit_rps
        self.daily_budget_usd = daily_budget_usd
        self._sources: Dict[str, Dict[str, Any]] = {}
        self._buckets: Dict[str, _TokenBucket] = {}
        self._blocked: Set[str] = set()
        self._usage: Dict[str, Dict[str, Any]] = {}
        self._daily_spent = 0.0

    def register_source(
        self,
        name: str,
        config: Dict[str, Any],
    ) -> None:
        """Register a data source.

        Config keys:
        - url: str -- endpoint URL
        - auth: dict -- authentication config
        - rate_limit: float -- requests per second (default: gateway default)
        - cost_per_call: float -- cost in USD per call
        - timeout: float -- request timeout in seconds
        - max_expected_cost: float -- max expected cost for scoring
        """
        self._sources[name] = config
        rate = config.get("rate_limit", self.rate_limit_rps)
        self._buckets[name] = _TokenBucket(rate=rate, capacity=int(rate * 2))
        self._usage[name] = {
            "calls": 0,
            "cost": 0.0,
            "failures": 0,
            "history": [],
        }

    def get_access_score(self, source: str) -> AccessScore:
        """Get the access score for a registered source."""
        if source not in self._sources:
            raise SourceNotFoundError(f"Source '{source}' not registered")
        config = self._sources[source]
        usage = self._usage.get(source, {})
        history = usage.get("history", [])
        score = compute_access_score(config, history)
        bucket = self._buckets.get(source)
        remaining = int(bucket.tokens) if bucket else 0
        return AccessScore(
            surface=source,
            score=score,
            rate_limit_remaining=remaining,
            cost_per_call_usd=config.get("cost_per_call", 0.0),
            risk_level="medium",
        )

    async def request(
        self,
        source: str,
        query: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Make a request respecting rate limits and budget."""
        if source in self._blocked:
            raise SourceBlockedError(f"Source '{source}' is blocked")
        if source not in self._sources:
            raise SourceNotFoundError(f"Source '{source}' not registered")
        if self._daily_spent >= self.daily_budget_usd:
            raise BudgetExceededError("Daily budget exceeded")

        bucket = self._buckets[source]
        if not bucket.consume():
            raise RateLimitExceededError(f"Rate limit exceeded for '{source}'")

        config = self._sources[source]
        cost = config.get("cost_per_call", 0.0)
        self._daily_spent += cost
        self._usage[source]["calls"] += 1
        self._usage[source]["cost"] += cost

        # Record successful call in history
        self._usage[source]["history"].append({
            "timestamp": time.time(),
            "success": True,
            "query": query,
        })

        # Mock response for this implementation
        return {
            "status": "ok",
            "data": query,
            "source": source,
            "timestamp": time.time(),
            "cost": cost,
        }

    def get_usage_report(self) -> Dict[str, Any]:
        """Get a usage report for all sources."""
        total_calls = sum(u["calls"] for u in self._usage.values())
        total_cost = sum(u["cost"] for u in self._usage.values())
        by_source = {
            name: {"calls": u["calls"], "cost": u["cost"]}
            for name, u in self._usage.items()
        }
        return {
            "total_calls": total_calls,
            "total_cost": total_cost,
            "by_source": by_source,
            "daily_budget": self.daily_budget_usd,
            "daily_spent": self._daily_spent,
            "remaining_budget": self.daily_budget_usd - self._daily_spent,
        }

    def block_source(self, source: str, reason: str = "") -> None:
        """Block a source from being accessed."""
        self._blocked.add(source)

    def unblock_source(self, source: str) -> None:
        """Unblock a previously blocked source."""
        self._blocked.discard(source)

    def is_blocked(self, source: str) -> bool:
        """Check if a source is blocked."""
        return source in self._blocked

    def get_registered_sources(self) -> list[str]:
        """List all registered source names."""
        return list(self._sources.keys())

    def reset_budget(self) -> None:
        """Reset the daily budget counter."""
        self._daily_spent = 0.0

    def reset_usage(self, source: Optional[str] = None) -> None:
        """Reset usage counters for a source or all sources."""
        if source:
            if source in self._usage:
                self._usage[source] = {
                    "calls": 0,
                    "cost": 0.0,
                    "failures": 0,
                    "history": [],
                }
        else:
            for name in self._usage:
                self._usage[name] = {
                    "calls": 0,
                    "cost": 0.0,
                    "failures": 0,
                    "history": [],
                }
