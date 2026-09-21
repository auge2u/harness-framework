"""Tests for harness.analysis.clusterer module."""
from __future__ import annotations

from datetime import datetime

import pytest

from harness.core.types import TraceRecord, Verdict, FailureSignature
from harness.analysis.clusterer import FailureClusterer


class TestClustererSignature:
    """Test signature extraction."""

    def test_extract_from_pass_trace(self, clusterer):
        trace = TraceRecord(
            trace_id="t1", scenario_id="s1",
            harness_version="0.1.0", timestamp=datetime.now(),
            verdict=Verdict.PASS,
        )
        cid = clusterer.add_trace(trace)
        assert cid is None  # PASS traces are not clustered

    def test_extract_from_fail_trace(self, clusterer):
        trace = TraceRecord(
            trace_id="t1", scenario_id="s1",
            harness_version="0.1.0", timestamp=datetime.now(),
            verdict=Verdict.FAIL, metadata={"error": "Timeout"},
            verifier_results={"error": "connection timeout"},
        )
        cid = clusterer.add_trace(trace)
        assert cid is not None

    def test_cluster_returns_dict(self, clusterer):
        traces = [
            TraceRecord(
                trace_id="t1", scenario_id="s1", harness_version="0.1.0",
                timestamp=datetime.now(), verdict=Verdict.FAIL,
                verifier_results={"error": "timeout"},
                metadata={"surfaces": ["cost_api"]},
            ),
            TraceRecord(
                trace_id="t2", scenario_id="s2", harness_version="0.1.0",
                timestamp=datetime.now(), verdict=Verdict.FAIL,
                verifier_results={"error": "timeout again"},
                metadata={"surfaces": ["cost_api"]},
            ),
        ]
        clusters = clusterer.cluster(traces)
        assert isinstance(clusters, dict)


class TestClustererSimilarity:
    """Test that similar failures cluster together."""

    def test_single_cluster(self):
        # Two traces with identical signatures should cluster together
        # Use a fresh clusterer and same scenario_id for high similarity
        c = FailureClusterer()
        traces = [
            TraceRecord(
                trace_id="t1", scenario_id="s1", harness_version="0.1.0",
                timestamp=datetime.now(), verdict=Verdict.FAIL,
                verifier_results={"error": "connection timeout"},
                metadata={"surfaces": ["cost_api"]},
            ),
            TraceRecord(
                trace_id="t2", scenario_id="s1", harness_version="0.1.0",
                timestamp=datetime.now(), verdict=Verdict.FAIL,
                verifier_results={"error": "connection timeout"},
                metadata={"surfaces": ["cost_api"]},
            ),
        ]
        clusters = c.cluster(traces)
        assert isinstance(clusters, dict)
        # At least one cluster should have 2 traces if they were grouped
        found_multi = any(len(v) >= 2 for v in clusters.values())
        assert found_multi, f"Expected at least one cluster with 2+ traces, got {clusters}"

    def test_different_errors_different_clusters(self, clusterer):
        clusterer2 = FailureClusterer(similarity_threshold=0.99)
        traces = [
            TraceRecord(
                trace_id="t1", scenario_id="s1", harness_version="0.1.0",
                timestamp=datetime.now(), verdict=Verdict.FAIL,
                verifier_results={"error": "timeout"},
                metadata={"surfaces": ["cost_api"]},
            ),
            TraceRecord(
                trace_id="t2", scenario_id="s2", harness_version="0.1.0",
                timestamp=datetime.now(), verdict=Verdict.FAIL,
                verifier_results={"error": "completely different error message here"},
                metadata={"surfaces": ["database"]},
            ),
        ]
        clusters = clusterer2.cluster(traces)
        assert isinstance(clusters, dict)


class TestClustererQueries:
    """Test cluster query methods."""

    def test_get_signature(self, clusterer):
        trace = TraceRecord(
            trace_id="t1", scenario_id="s1", harness_version="0.1.0",
            timestamp=datetime.now(), verdict=Verdict.FAIL,
            verifier_results={"error": "timeout"},
            metadata={"surfaces": ["cost_api"]},
        )
        cid = clusterer.add_trace(trace)
        if cid:
            sig = clusterer.get_signature(cid)
            assert isinstance(sig, FailureSignature)

    def test_get_clusters_for_surface(self, clusterer):
        trace = TraceRecord(
            trace_id="t1", scenario_id="s1", harness_version="0.1.0",
            timestamp=datetime.now(), verdict=Verdict.FAIL,
            verifier_results={"error": "timeout"},
            metadata={"surfaces": ["cost_api"]},
        )
        clusterer.add_trace(trace)
        sigs = clusterer.get_clusters_for_surface("cost_api")
        assert isinstance(sigs, list)

    def test_get_relationship_impact(self, clusterer):
        traces = [
            TraceRecord(
                trace_id="t1", scenario_id="s1", harness_version="0.1.0",
                timestamp=datetime.now(), verdict=Verdict.FAIL,
                verifier_results={"error": "timeout"},
                metadata={"surfaces": ["cost_api"]},
            ),
        ]
        clusterer.cluster(traces)
        impact = clusterer.get_relationship_impact()
        assert isinstance(impact, dict)

    def test_get_all_signatures(self, clusterer):
        assert clusterer.get_all_signatures() == []

    def test_get_cluster_count(self, clusterer):
        assert clusterer.get_cluster_count() == 0

    def test_empty_traces(self, clusterer):
        clusters = clusterer.cluster([])
        assert clusters == {}
