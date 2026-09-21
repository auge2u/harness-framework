"""Tests for harness.store.trace_store module."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta

from harness.core.types import TraceRecord, Verdict
from harness.store.trace_store import TraceStore


class TestTraceStoreRecord:
    """Test trace recording."""

    def test_record_basic(self, trace_store):
        trace = TraceRecord(
            trace_id="t1",
            scenario_id="s1",
            harness_version="0.1.0",
            timestamp=datetime.now(),
            verdict=Verdict.PASS,
        )
        trace_store.record(trace)
        results = trace_store.query()
        assert len(results) == 1
        assert results[0].trace_id == "t1"

    def test_record_multiple(self, trace_store):
        for i in range(5):
            trace = TraceRecord(
                trace_id=f"t{i}",
                scenario_id=f"s{i % 2}",
                harness_version="0.1.0",
                timestamp=datetime.now(),
                verdict=Verdict.PASS,
            )
            trace_store.record(trace)
        assert len(trace_store.query()) == 5

    def test_record_with_inputs_outputs(self, trace_store):
        trace = TraceRecord(
            trace_id="t1", scenario_id="s1",
            harness_version="0.1.0", timestamp=datetime.now(),
            inputs={"query": "test"}, outputs={"result": "ok"},
            verdict=Verdict.PASS,
        )
        trace_store.record(trace)
        results = trace_store.query()
        assert results[0].inputs == {"query": "test"}
        assert results[0].outputs == {"result": "ok"}


class TestTraceStoreQuery:
    """Test trace querying with filters."""

    def test_query_by_scenario_id(self, trace_store):
        trace_store.record(TraceRecord(trace_id="t1", scenario_id="s1", harness_version="0.1.0", timestamp=datetime.now(), verdict=Verdict.PASS))
        trace_store.record(TraceRecord(trace_id="t2", scenario_id="s2", harness_version="0.1.0", timestamp=datetime.now(), verdict=Verdict.PASS))
        results = trace_store.query(scenario_id="s1")
        assert len(results) == 1
        assert results[0].trace_id == "t1"

    def test_query_by_verdict(self, trace_store):
        trace_store.record(TraceRecord(trace_id="t1", scenario_id="s1", harness_version="0.1.0", timestamp=datetime.now(), verdict=Verdict.PASS))
        trace_store.record(TraceRecord(trace_id="t2", scenario_id="s1", harness_version="0.1.0", timestamp=datetime.now(), verdict=Verdict.FAIL))
        results = trace_store.query(verdict=Verdict.FAIL)
        assert len(results) == 1
        assert results[0].trace_id == "t2"

    def test_query_by_surface(self, trace_store):
        trace = TraceRecord(
            trace_id="t1", scenario_id="s1",
            harness_version="0.1.0", timestamp=datetime.now(),
            verdict=Verdict.PASS, metadata={"surfaces": ["cost_api"]},
        )
        trace_store.record(trace)
        results = trace_store.query(surface="cost_api")
        assert len(results) == 1

    def test_query_since(self, trace_store):
        trace_store.record(TraceRecord(trace_id="t1", scenario_id="s1", harness_version="0.1.0", timestamp=datetime.now() - timedelta(days=2), verdict=Verdict.PASS))
        trace_store.record(TraceRecord(trace_id="t2", scenario_id="s1", harness_version="0.1.0", timestamp=datetime.now(), verdict=Verdict.PASS))
        results = trace_store.query(since=(datetime.now() - timedelta(hours=1)).isoformat())
        assert len(results) == 1
        assert results[0].trace_id == "t2"

    def test_query_no_match(self, trace_store):
        trace_store.record(TraceRecord(trace_id="t1", scenario_id="s1", harness_version="0.1.0", timestamp=datetime.now(), verdict=Verdict.PASS))
        results = trace_store.query(scenario_id="nonexistent")
        assert results == []


class TestTraceStoreCost:
    """Test cost-related methods."""

    def test_get_cost_by_surface(self, trace_store):
        trace_store.record(TraceRecord(trace_id="t1", scenario_id="s1", harness_version="0.1.0", timestamp=datetime.now(), cost_usd=0.5, metadata={"surfaces": ["cost_api"]}))
        trace_store.record(TraceRecord(trace_id="t2", scenario_id="s1", harness_version="0.1.0", timestamp=datetime.now(), cost_usd=1.2, metadata={"surfaces": ["cost_api"]}))
        assert abs(trace_store.get_cost_by_surface("cost_api") - 1.7) < 0.001

    def test_get_cost_by_surface_no_match(self, trace_store):
        assert trace_store.get_cost_by_surface("nonexistent") == 0.0

    def test_get_surface_failure_rate(self, trace_store):
        trace_store.record(TraceRecord(trace_id="t1", scenario_id="s1", harness_version="0.1.0", timestamp=datetime.now(), verdict=Verdict.PASS, metadata={"surfaces": ["cost_api"]}))
        trace_store.record(TraceRecord(trace_id="t2", scenario_id="s1", harness_version="0.1.0", timestamp=datetime.now(), verdict=Verdict.FAIL, metadata={"surfaces": ["cost_api"]}))
        rate = trace_store.get_surface_failure_rate("cost_api")
        assert rate == 0.5


class TestTraceStorePerformance:
    """Test performance trend methods."""

    def test_get_performance_trend(self, trace_store):
        for i in range(3):
            trace_store.record(TraceRecord(
                trace_id=f"t{i}", scenario_id="s1",
                harness_version="0.1.0", timestamp=datetime.now(),
                verdict=Verdict.PASS,
                verifier_results={"aggregate_score": float(i + 1) * 0.3},
            ))
        trend = trace_store.get_performance_trend("s1", window=5)
        assert len(trend) == 3

    def test_export_json(self, trace_store, tmp_path):
        trace_store.record(TraceRecord(trace_id="t1", scenario_id="s1", harness_version="0.1.0", timestamp=datetime.now(), verdict=Verdict.PASS))
        export_path = str(tmp_path / "export.json")
        trace_store.export(export_path, format="json")
        import json
        with open(export_path) as f:
            data = json.load(f)
        assert len(data) == 1

    def test_export_csv(self, trace_store, tmp_path):
        trace_store.record(TraceRecord(trace_id="t1", scenario_id="s1", harness_version="0.1.0", timestamp=datetime.now(), verdict=Verdict.PASS))
        export_path = str(tmp_path / "export.csv")
        trace_store.export(export_path, format="csv")
        import csv
        with open(export_path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 1
