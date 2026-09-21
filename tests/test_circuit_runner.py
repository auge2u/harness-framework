"""Tests for harness.runners.circuit_runner module."""
from __future__ import annotations

import pytest

from harness.core.config import HarnessConfig
from harness.core.types import HarnessProposal
from harness.runners.circuit_runner import CircuitResult, CircuitRunner
from harness.scenarios.split import ScenarioSplit
from harness.store.trace_store import TraceStore


class TestCircuitResult:
    """Test CircuitResult dataclass."""

    def test_defaults(self):
        r = CircuitResult(variant_id="v1", config_version="0.1.0")
        assert r.aggregate_score == 0.0
        assert r.cost_usd == 0.0
        assert r.traces == []


class TestCircuitRunner:
    """Test CircuitRunner initialization and helpers."""

    def test_init(self, registry):
        store = TraceStore(":memory:")
        cr = CircuitRunner(registry, store, max_parallel=2, cost_budget_usd=5.0)
        assert cr.max_parallel == 2
        assert cr.cost_budget_usd == 5.0

    def test_select_best_empty_raises(self, registry):
        store = TraceStore(":memory:")
        cr = CircuitRunner(registry, store)
        with pytest.raises(ValueError):
            cr.select_best([])

    def test_select_best(self, registry):
        store = TraceStore(":memory:")
        cr = CircuitRunner(registry, store)
        results = [
            CircuitResult(variant_id="a", config_version="1", aggregate_score=0.5),
            CircuitResult(variant_id="b", config_version="1", aggregate_score=0.9),
        ]
        best = cr.select_best(results)
        assert best.variant_id == "b"

    def test_compare_empty(self, registry):
        store = TraceStore(":memory:")
        cr = CircuitRunner(registry, store)
        comparison = cr.compare([])
        assert comparison["best_variant_id"] is None
