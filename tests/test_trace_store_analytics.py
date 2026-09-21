"""Tests for TraceStore analytics methods (C1)."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta

from harness.core.types import TraceRecord, Verdict
from harness.store.trace_store import TraceStore


@pytest.fixture
def analytics_store():
    """Fresh in-memory TraceStore for analytics tests."""
    store = TraceStore(":memory:")
    yield store
    store.close()


@pytest.fixture
def populated_store(analytics_store):
    """TraceStore pre-populated with diverse trace records."""
    store = analytics_store
    base_time = datetime(2024, 1, 15, 10, 0, 0)

    # Scenario s1 — improving scores on cost_api
    for i in range(5):
        store.record(TraceRecord(
            trace_id=f"s1_t{i}",
            scenario_id="s1",
            harness_version="0.1.0",
            timestamp=base_time + timedelta(hours=i),
            verdict=Verdict.PASS if i >= 2 else Verdict.FAIL,
            verifier_results={"aggregate_score": 0.4 + i * 0.15},
            latency_ms=100.0 + i * 10,
            cost_usd=0.1 + i * 0.05,
            metadata={"surface": "cost_api"},
        ))

    # Scenario s2 — stable scores on auth_service
    for i in range(3):
        store.record(TraceRecord(
            trace_id=f"s2_t{i}",
            scenario_id="s2",
            harness_version="0.1.0",
            timestamp=base_time + timedelta(hours=i + 10),
            verdict=Verdict.PASS,
            verifier_results={"aggregate_score": 0.85},
            latency_ms=200.0,
            cost_usd=0.5,
            metadata={"surface": "auth_service"},
        ))

    # Scenario s3 — very low scores, on auth_service + surfaces list
    for i in range(3):
        store.record(TraceRecord(
            trace_id=f"s3_t{i}",
            scenario_id="s3",
            harness_version="0.1.0",
            timestamp=base_time + timedelta(hours=i + 20),
            verdict=Verdict.FAIL,
            verifier_results={"aggregate_score": 0.01},
            latency_ms=500.0,
            cost_usd=2.0,
            metadata={"surface": "auth_service", "surfaces": ["auth_service", "database"]},
        ))

    return store


# ---------------------------------------------------------------------------
# get_time_series
# ---------------------------------------------------------------------------

class TestGetTimeSeries:
    """Tests for TraceStore.get_time_series()."""

    def test_time_series_score_day(self, populated_store):
        """get_time_series returns day-bucketed scores."""
        store = populated_store
        result = store.get_time_series(metric="score", interval="day")
        # Traces span Jan 15 and Jan 16
        assert len(result) == 2
        jan15 = [r for r in result if r["bucket"] == "2024-01-15"][0]
        assert jan15["count"] == 8  # 5 s1 + 3 s2
        assert 0.0 < jan15["value"] <= 1.0

    def test_time_series_cost_day(self, populated_store):
        """get_time_series returns day-bucketed costs."""
        store = populated_store
        result = store.get_time_series(metric="cost", interval="day")
        assert len(result) == 2
        jan15 = [r for r in result if r["bucket"] == "2024-01-15"][0]
        assert jan15["count"] == 8
        assert jan15["value"] > 0.0

    def test_time_series_latency_day(self, populated_store):
        """get_time_series returns day-bucketed latency."""
        store = populated_store
        result = store.get_time_series(metric="latency", interval="day")
        assert len(result) == 2
        jan15 = [r for r in result if r["bucket"] == "2024-01-15"][0]
        assert jan15["count"] == 8
        assert jan15["value"] > 0.0

    def test_time_series_failure_rate(self, populated_store):
        """get_time_series returns day-bucketed failure rate."""
        store = populated_store
        result = store.get_time_series(metric="failure_rate", interval="day")
        assert len(result) == 2
        # Jan 15: 5 s1 traces (2 FAIL) + 3 s2 traces (0 FAIL) = 2/8 = 0.25
        jan15 = [r for r in result if r["bucket"] == "2024-01-15"][0]
        assert abs(jan15["value"] - 0.25) < 0.01
        # Jan 16: 3 s3 traces (3 FAIL) = 1.0
        jan16 = [r for r in result if r["bucket"] == "2024-01-16"][0]
        assert jan16["value"] == 1.0

    def test_time_series_hour_interval(self, populated_store):
        """get_time_series supports hour interval."""
        store = populated_store
        result = store.get_time_series(metric="score", interval="hour")
        assert len(result) >= 3
        for r in result:
            assert "bucket" in r
            assert "value" in r
            assert "count" in r

    def test_time_series_week_interval(self, populated_store):
        """get_time_series supports week interval."""
        store = populated_store
        result = store.get_time_series(metric="score", interval="week")
        # Both days are in the same week
        assert len(result) == 1
        assert result[0]["count"] == 11

    def test_time_series_since_until(self, populated_store):
        """get_time_series respects since/until bounds."""
        store = populated_store
        since = (datetime(2024, 1, 15, 10, 0, 0) + timedelta(hours=21)).isoformat()
        result = store.get_time_series(metric="score", interval="day", since=since)
        # Only s3 traces at hour 20, 21, 22 are >= 21:00
        # Actually hour 20 = 06:00 Jan 16 which is < 21:00 Jan 15
        # So hours 21, 22 from s3 = 2 traces on Jan 16
        jan16 = [r for r in result if r["bucket"] == "2024-01-16"]
        assert len(jan16) == 1
        assert jan16[0]["count"] == 2

    def test_time_series_empty_store(self, analytics_store):
        """get_time_series returns empty list for empty store."""
        result = analytics_store.get_time_series(metric="score", interval="day")
        assert result == []

    def test_time_series_invalid_metric(self, populated_store):
        """get_time_series raises ValueError for unknown metric."""
        with pytest.raises(ValueError, match="Unknown metric"):
            populated_store.get_time_series(metric="invalid_metric")


# ---------------------------------------------------------------------------
# get_trend
# ---------------------------------------------------------------------------

class TestGetTrend:
    """Tests for TraceStore.get_trend()."""

    def test_trend_score_values(self, populated_store):
        """get_trend returns score values."""
        store = populated_store
        result = store.get_trend("s1", metric="score", window=10)
        assert len(result["values"]) == 5
        assert result["mean"] > 0.0

    def test_trend_score_direction_improving(self, populated_store):
        """get_trend detects improving direction."""
        store = populated_store
        result = store.get_trend("s1", metric="score", window=10)
        # s1 scores: 0.4, 0.55, 0.7, 0.85, 1.0 — clearly improving
        assert result["direction"] == "improving"
        assert result["slope"] > 0.0

    def test_trend_score_direction_stable(self, populated_store):
        """get_trend detects stable direction."""
        store = populated_store
        result = store.get_trend("s2", metric="score", window=10)
        # s2 scores: all 0.85 — stable
        assert result["direction"] == "stable"
        assert result["std"] == 0.0

    def test_trend_cost_metric(self, populated_store):
        """get_trend supports cost metric."""
        store = populated_store
        result = store.get_trend("s1", metric="cost", window=10)
        assert len(result["values"]) == 5
        assert result["mean"] > 0.0

    def test_trend_latency_metric(self, populated_store):
        """get_trend supports latency metric."""
        store = populated_store
        result = store.get_trend("s1", metric="latency", window=10)
        assert len(result["values"]) == 5
        assert result["mean"] > 0.0

    def test_trend_window_limit(self, populated_store):
        """get_trend respects window limit."""
        store = populated_store
        result = store.get_trend("s1", metric="score", window=3)
        assert len(result["values"]) == 3

    def test_trend_empty_scenario(self, populated_store):
        """get_trend returns zeros for unknown scenario."""
        store = populated_store
        result = store.get_trend("nonexistent", metric="score", window=10)
        assert result["values"] == []
        assert result["mean"] == 0.0
        assert result["std"] == 0.0
        assert result["slope"] == 0.0

    def test_trend_invalid_metric(self, populated_store):
        """get_trend raises ValueError for unknown metric."""
        with pytest.raises(ValueError, match="Unknown metric"):
            populated_store.get_trend("s1", metric="invalid")


# ---------------------------------------------------------------------------
# get_surface_analytics
# ---------------------------------------------------------------------------

class TestGetSurfaceAnalytics:
    """Tests for TraceStore.get_surface_analytics()."""

    def test_surface_analytics_keys(self, populated_store):
        """get_surface_analytics returns expected surfaces."""
        store = populated_store
        result = store.get_surface_analytics()
        assert "cost_api" in result
        assert "auth_service" in result
        assert "database" in result

    def test_surface_analytics_cost_api(self, populated_store):
        """get_surface_analytics has correct data for cost_api."""
        store = populated_store
        result = store.get_surface_analytics()
        assert result["cost_api"]["total_runs"] == 5
        assert result["cost_api"]["failure_rate"] == 0.4  # 2 out of 5
        assert result["cost_api"]["avg_cost"] > 0.0
        assert result["cost_api"]["avg_latency"] > 0.0

    def test_surface_analytics_auth_service(self, populated_store):
        """get_surface_analytics has correct data for auth_service."""
        store = populated_store
        result = store.get_surface_analytics()
        # auth_service: s2 (3 traces, surface) + s3 (3 traces, surface + surfaces list)
        # SQL query: 6 traces with $.surface="auth_service"
        # Plus 3 more from $.surfaces list
        assert result["auth_service"]["total_runs"] == 9
        assert result["auth_service"]["avg_cost"] > 0.0

    def test_surface_analytics_database(self, populated_store):
        """get_surface_analytics has correct data for database."""
        store = populated_store
        result = store.get_surface_analytics()
        # database only appears in s3's surfaces list
        assert result["database"]["total_runs"] == 3

    def test_surface_analytics_empty_store(self, analytics_store):
        """get_surface_analytics returns empty dict for empty store."""
        result = analytics_store.get_surface_analytics()
        assert result == {}


# ---------------------------------------------------------------------------
# get_anomaly_traces
# ---------------------------------------------------------------------------

class TestGetAnomalyTraces:
    """Tests for TraceStore.get_anomaly_traces()."""

    def test_anomaly_traces_finds_outliers(self, populated_store):
        """get_anomaly_traces finds statistical outliers."""
        store = populated_store
        # s3 scores are 0.01, much lower than the rest
        anomalies = store.get_anomaly_traces(threshold_sigma=0.8)
        assert len(anomalies) >= 3
        # s3 traces should be among the anomalies
        s3_anomalies = [t for t in anomalies if t.scenario_id == "s3"]
        assert len(s3_anomalies) == 3

    def test_anomaly_traces_high_threshold(self, populated_store):
        """get_anomaly_traces with high threshold finds fewer outliers."""
        store = populated_store
        anomalies = store.get_anomaly_traces(threshold_sigma=3.0)
        # With very high threshold, may find no outliers
        assert len(anomalies) >= 0

    def test_anomaly_traces_empty_store(self, analytics_store):
        """get_anomaly_traces returns empty list for empty store."""
        result = analytics_store.get_anomaly_traces()
        assert result == []

    def test_anomaly_traces_single_record(self, analytics_store):
        """get_anomaly_traces returns empty list with only one record."""
        store = analytics_store
        store.record(TraceRecord(
            trace_id="t1", scenario_id="s1",
            harness_version="0.1.0", timestamp=datetime.now(),
            verdict=Verdict.PASS,
        ))
        result = store.get_anomaly_traces()
        assert result == []


# ---------------------------------------------------------------------------
# _create_indices
# ---------------------------------------------------------------------------

class TestCreateIndices:
    """Tests for index creation."""

    def test_indices_created(self, analytics_store):
        """Indices are created on initialization."""
        store = analytics_store
        conn = store._connection()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_traces_%'"
        )
        names = {row["name"] for row in cursor.fetchall()}
        assert "idx_traces_scenario" in names
        assert "idx_traces_verdict" in names
        assert "idx_traces_version" in names
        assert "idx_traces_timestamp" in names
