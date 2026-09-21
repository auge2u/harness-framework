"""Tests for the DataGateway and access scoring (F6)."""
from __future__ import annotations

import asyncio

import pytest

from harness.core.exceptions import (
    BudgetExceededError,
    RateLimitExceededError,
    SourceBlockedError,
    SourceNotFoundError,
)
from harness.gateway.data_gateway import DataGateway, _TokenBucket
from harness.gateway.scoring import AccessScore, compute_access_score


class TestDataGatewayRegistration:
    """Tests for source registration."""

    def test_register_source(self):
        """register_source adds a source."""
        gw = DataGateway()
        gw.register_source("api", {"url": "https://api.example.com"})
        assert "api" in gw._sources

    def test_get_registered_sources(self):
        """get_registered_sources returns all source names."""
        gw = DataGateway()
        gw.register_source("a", {"url": "https://a.com"})
        gw.register_source("b", {"url": "https://b.com"})
        sources = gw.get_registered_sources()
        assert set(sources) == {"a", "b"}

    def test_get_access_score_unregistered_raises(self):
        """get_access_score raises SourceNotFoundError for unknown source."""
        gw = DataGateway()
        with pytest.raises(SourceNotFoundError):
            gw.get_access_score("nonexistent")


class TestDataGatewayRequest:
    """Tests for async request handling."""

    def test_request_basic(self):
        """request returns a response dict."""
        gw = DataGateway()
        gw.register_source("api", {"url": "https://api.example.com", "cost_per_call": 0.01})

        result = asyncio.run(gw.request("api", {"query": "test"}))
        assert result["status"] == "ok"
        assert result["source"] == "api"
        assert "timestamp" in result

    def test_request_unregistered_raises(self):
        """request raises SourceNotFoundError for unknown source."""
        gw = DataGateway()
        with pytest.raises(SourceNotFoundError):
            asyncio.run(gw.request("nonexistent", {}))

    def test_request_blocked_source_raises(self):
        """request raises SourceBlockedError for blocked source."""
        gw = DataGateway()
        gw.register_source("api", {"url": "https://api.example.com"})
        gw.block_source("api", "test block")
        with pytest.raises(SourceBlockedError):
            asyncio.run(gw.request("api", {}))

    def test_request_budget_exceeded_raises(self):
        """request raises BudgetExceededError when daily budget is hit."""
        gw = DataGateway(daily_budget_usd=0.05)
        gw.register_source("api", {"url": "https://api.example.com", "cost_per_call": 0.1})
        # First request: budget is 0.05, cost is 0.1 -> exceeds
        with pytest.raises(BudgetExceededError):
            asyncio.run(gw.request("api", {}))

    def test_request_rate_limit_raises(self):
        """request raises RateLimitExceededError when rate limit hit."""
        gw = DataGateway(rate_limit_rps=0.1)
        gw.register_source("api", {"url": "https://api.example.com", "rate_limit": 0.1})
        # Consume all tokens
        bucket = gw._buckets["api"]
        bucket.tokens = 0
        with pytest.raises(RateLimitExceededError):
            asyncio.run(gw.request("api", {}))

    def test_request_updates_usage(self):
        """request updates usage stats."""
        gw = DataGateway()
        gw.register_source("api", {"url": "https://api.example.com", "cost_per_call": 0.01})
        asyncio.run(gw.request("api", {"q": 1}))
        asyncio.run(gw.request("api", {"q": 2}))
        usage = gw._usage["api"]
        assert usage["calls"] == 2
        assert usage["cost"] == pytest.approx(0.02)


class TestDataGatewayBlocking:
    """Tests for source blocking."""

    def test_block_and_unblock(self):
        """block_source and unblock_source work correctly."""
        gw = DataGateway()
        gw.register_source("api", {"url": "https://api.example.com"})
        assert not gw.is_blocked("api")
        gw.block_source("api", "maintenance")
        assert gw.is_blocked("api")
        gw.unblock_source("api")
        assert not gw.is_blocked("api")


class TestDataGatewayUsageReport:
    """Tests for usage reporting."""

    def test_get_usage_report_empty(self):
        """get_usage_report with no calls returns zeros."""
        gw = DataGateway()
        report = gw.get_usage_report()
        assert report["total_calls"] == 0
        assert report["total_cost"] == 0.0
        assert report["by_source"] == {}

    def test_get_usage_report_with_calls(self):
        """get_usage_report tracks calls and cost per source."""
        gw = DataGateway()
        gw.register_source("a", {"url": "https://a.com", "cost_per_call": 0.01})
        gw.register_source("b", {"url": "https://b.com", "cost_per_call": 0.02})
        asyncio.run(gw.request("a", {}))
        asyncio.run(gw.request("a", {}))
        asyncio.run(gw.request("b", {}))
        report = gw.get_usage_report()
        assert report["total_calls"] == 3
        assert report["total_cost"] == pytest.approx(0.04)
        assert report["by_source"]["a"]["calls"] == 2
        assert report["by_source"]["b"]["calls"] == 1


class TestDataGatewayBudget:
    """Tests for budget management."""

    def test_reset_budget(self):
        """reset_budget resets daily spent."""
        gw = DataGateway()
        gw.register_source("api", {"url": "https://api.example.com", "cost_per_call": 0.01})
        asyncio.run(gw.request("api", {}))
        assert gw._daily_spent > 0
        gw.reset_budget()
        assert gw._daily_spent == 0.0

    def test_reset_usage_single_source(self):
        """reset_usage resets stats for one source."""
        gw = DataGateway()
        gw.register_source("api", {"url": "https://api.example.com", "cost_per_call": 0.01})
        asyncio.run(gw.request("api", {}))
        gw.reset_usage("api")
        assert gw._usage["api"]["calls"] == 0

    def test_reset_usage_all(self):
        """reset_usage with no args resets all sources."""
        gw = DataGateway()
        gw.register_source("a", {"url": "https://a.com", "cost_per_call": 0.01})
        gw.register_source("b", {"url": "https://b.com", "cost_per_call": 0.01})
        asyncio.run(gw.request("a", {}))
        asyncio.run(gw.request("b", {}))
        gw.reset_usage()
        assert gw._usage["a"]["calls"] == 0
        assert gw._usage["b"]["calls"] == 0


class TestTokenBucket:
    """Tests for token bucket rate limiter."""

    def test_initial_tokens(self):
        """Token bucket starts full."""
        bucket = _TokenBucket(rate=10.0, capacity=20)
        assert bucket.tokens == 20.0

    def test_consume_reduces_tokens(self):
        """consume reduces tokens."""
        bucket = _TokenBucket(rate=10.0, capacity=20)
        assert bucket.consume(5)
        assert bucket.tokens == pytest.approx(15.0)

    def test_consume_insufficient_tokens(self):
        """consume fails when insufficient tokens."""
        bucket = _TokenBucket(rate=10.0, capacity=20)
        bucket.tokens = 3
        assert not bucket.consume(5)

    def test_refill_restores_tokens(self):
        """refill restores tokens."""
        bucket = _TokenBucket(rate=10.0, capacity=20)
        bucket.tokens = 0
        bucket.refill(10)
        assert bucket.tokens == 10.0

    def test_refill_no_arg_full(self):
        """refill with no args restores to capacity."""
        bucket = _TokenBucket(rate=10.0, capacity=20)
        bucket.tokens = 0
        bucket.refill()
        assert bucket.tokens == 20.0


class TestAccessScore:
    """Tests for access scoring."""

    def test_access_score_creation(self):
        """AccessScore can be created with required fields."""
        score = AccessScore(
            surface="api",
            score=0.75,
            rate_limit_remaining=10,
            cost_per_call_usd=0.01,
        )
        assert score.surface == "api"
        assert score.score == 0.75
        assert score.risk_level == "low"  # >= 0.7

    def test_access_score_risk_medium(self):
        """AccessScore with medium score gets medium risk."""
        score = AccessScore(surface="api", score=0.5, rate_limit_remaining=5, cost_per_call_usd=0.01)
        assert score.risk_level == "medium"

    def test_access_score_risk_high(self):
        """AccessScore with low score gets high risk."""
        score = AccessScore(surface="api", score=0.2, rate_limit_remaining=5, cost_per_call_usd=0.01)
        assert score.risk_level == "high"

    def test_compute_access_score_empty_history(self):
        """compute_access_score with empty history returns valid score."""
        score = compute_access_score({}, [])
        assert 0.0 <= score <= 1.0

    def test_compute_access_score_with_history(self):
        """compute_access_score with history returns valid score."""
        history = [
            {"timestamp": 1000, "success": True},
            {"timestamp": 2000, "success": True},
            {"timestamp": 3000, "success": False},
        ]
        config = {"cost_per_call": 0.01, "max_expected_cost": 0.1}
        score = compute_access_score(config, history)
        assert 0.0 <= score <= 1.0
