"""Tests for harness.telemetry module."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta

from harness.core.types import TraceRecord, Verdict
from harness.telemetry.metrics import TelemetryCollector, TelemetryEvent
from harness.telemetry.finops import FinOpsMetrics
from harness.store.trace_store import TraceStore


class TestTelemetryEmit:
    """Test telemetry event emission."""

    def test_emit_records_trace(self, trace_store):
        collector = TelemetryCollector(trace_store)
        event = TelemetryEvent(
            event_type="scenario_start",
            timestamp=datetime.now(),
            harness_version="0.1.0",
            cost_usd=0.01,
        )
        collector.emit(event)
        traces = trace_store.query()
        assert len(traces) == 1

    def test_emit_multiple_events(self, trace_store):
        collector = TelemetryCollector(trace_store)
        for i in range(5):
            collector.emit(TelemetryEvent(
                event_type=f"event_{i}",
                timestamp=datetime.now(),
                harness_version="0.1.0",
            ))
        assert len(trace_store.query()) == 5

    def test_emit_with_surface(self, trace_store):
        collector = TelemetryCollector(trace_store)
        collector.emit(TelemetryEvent(
            event_type="test",
            timestamp=datetime.now(),
            harness_version="0.1.0",
            surface="cost_api",
        ))
        traces = trace_store.query()
        assert traces[0].metadata.get("surface") == "cost_api"


class TestTelemetryCostBySurface:
    """Test cost aggregation by surface."""

    def test_cost_by_surface_empty(self, trace_store):
        collector = TelemetryCollector(trace_store)
        costs = collector.get_cost_by_surface()
        assert costs == {}

    def test_cost_by_surface_single(self, trace_store):
        collector = TelemetryCollector(trace_store)
        trace_store.record(TraceRecord(
            trace_id="t1", scenario_id="s1",
            harness_version="0.1.0", timestamp=datetime.now(),
            cost_usd=1.0, metadata={"surfaces": ["cost_api"]},
        ))
        costs = collector.get_cost_by_surface()
        assert costs.get("cost_api") == 1.0

    def test_cost_by_surface_multiple(self, trace_store):
        collector = TelemetryCollector(trace_store)
        trace_store.record(TraceRecord(
            trace_id="t1", scenario_id="s1",
            harness_version="0.1.0", timestamp=datetime.now(),
            cost_usd=2.0, metadata={"surfaces": ["cost_api", "database"]},
        ))
        costs = collector.get_cost_by_surface()
        assert "cost_api" in costs
        assert "database" in costs


class TestTelemetryCostByScenario:
    """Test cost aggregation by scenario."""

    def test_cost_by_scenario(self, trace_store):
        collector = TelemetryCollector(trace_store)
        trace_store.record(TraceRecord(
            trace_id="t1", scenario_id="s1",
            harness_version="0.1.0", timestamp=datetime.now(),
            cost_usd=1.0,
        ))
        trace_store.record(TraceRecord(
            trace_id="t2", scenario_id="s2",
            harness_version="0.1.0", timestamp=datetime.now(),
            cost_usd=2.0,
        ))
        costs = collector.get_cost_by_scenario()
        assert costs.get("s1", 0) == 1.0
        assert costs.get("s2", 0) == 2.0


class TestTelemetryEfficiency:
    """Test efficiency score calculation."""

    def test_efficiency_with_cost(self, trace_store):
        collector = TelemetryCollector(trace_store)
        trace_store.record(TraceRecord(
            trace_id="t1", scenario_id="s1",
            harness_version="0.1.0", timestamp=datetime.now(),
            verdict=Verdict.PASS, cost_usd=2.0,
        ))
        score = collector.get_efficiency_score()
        assert score == 0.5  # 1 pass / $2

    def test_efficiency_no_cost(self, trace_store):
        collector = TelemetryCollector(trace_store)
        score = collector.get_efficiency_score()
        assert score == 0.0


class TestFinOpsExport:
    """Test FinOps metrics export."""

    def test_export_structure(self, trace_store):
        collector = TelemetryCollector(trace_store)
        finops = FinOpsMetrics(collector)
        export = finops.export()
        assert "schema_version" in export
        assert "summary" in export
        assert "cost_by_surface" in export
        assert "cost_by_scenario" in export

    def test_export_summary(self, trace_store):
        collector = TelemetryCollector(trace_store)
        trace_store.record(TraceRecord(
            trace_id="t1", scenario_id="s1",
            harness_version="0.1.0", timestamp=datetime.now(),
            cost_usd=1.0, metadata={"surfaces": ["cost_api"]},
        ))
        finops = FinOpsMetrics(collector)
        export = finops.export()
        assert export["summary"]["total_cost"] == 1.0


class TestFinOpsForecast:
    """Test budget forecasting."""

    def test_forecast_empty(self, trace_store):
        collector = TelemetryCollector(trace_store)
        finops = FinOpsMetrics(collector)
        forecast = finops.get_budget_forecast()
        assert forecast == 0.0


class TestFinOpsAlert:
    """Test budget alerting."""

    def test_alert_under_budget(self, trace_store):
        collector = TelemetryCollector(trace_store)
        finops = FinOpsMetrics(collector)
        alert = finops.alert_if_over_budget(budget=100.0)
        assert alert is None

    def test_alert_over_budget(self, trace_store):
        collector = TelemetryCollector(trace_store)
        now = datetime.now()
        for i in range(7):
            trace_store.record(TraceRecord(
                trace_id=f"t{i}", scenario_id="s1",
                harness_version="0.1.0", timestamp=now - timedelta(days=i),
                cost_usd=10.0,
            ))
        finops = FinOpsMetrics(collector)
        alert = finops.alert_if_over_budget(budget=50.0, threshold_ratio=0.5)
        assert alert is not None
        assert "ALERT" in alert

    def test_get_cost_variance(self, trace_store):
        collector = TelemetryCollector(trace_store)
        finops = FinOpsMetrics(collector)
        variance = finops.get_cost_variance()
        assert "mean" in variance

    def test_get_top_cost_surfaces(self, trace_store):
        collector = TelemetryCollector(trace_store)
        trace_store.record(TraceRecord(
            trace_id="t1", scenario_id="s1",
            harness_version="0.1.0", timestamp=datetime.now(),
            cost_usd=5.0, metadata={"surfaces": ["cost_api"]},
        ))
        finops = FinOpsMetrics(collector)
        top = finops.get_top_cost_surfaces(n=5)
        assert len(top) <= 5
        if top:
            assert top[0][0] == "cost_api"
