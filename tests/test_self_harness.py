"""Tests for harness.loops.self_harness module."""
from __future__ import annotations

import pytest

from harness.core.types import HarnessProposal
from harness.core.config import HarnessConfig
from harness.loops.self_harness import SelfHarnessLoop
from harness.scenarios.base import Scenario
from harness.scenarios.split import ScenarioSplit
from harness.store.trace_store import TraceStore
from harness.analysis.lineage import HarnessLineage
from harness.analysis.clusterer import FailureClusterer


class SimpleScenario(Scenario):
    """Simple scenario for testing."""
    scenario_id = "simple_1"
    name = "Simple"
    description = "A simple scenario"
    tags = ["test"]
    surfaces = ["cost_api"]
    difficulty = 0.5

    def setup(self):
        return {"value": 1}

    def run(self, agent, context):
        return {"result": context["value"] * 2}

    def teardown(self, context):
        pass

    def get_expected(self):
        return {"result": 2}


@pytest.fixture
def simple_scenarios():
    return [SimpleScenario()]


@pytest.fixture
def self_harness_loop(sample_config, registry, simple_scenarios, tmp_path):
    trace_store = TraceStore(":memory:")
    lineage = HarnessLineage(str(tmp_path / ".harness_lineage"))
    clusterer = FailureClusterer()
    loop = SelfHarnessLoop(sample_config, registry, trace_store, lineage, clusterer)
    return loop


class TestSelfHarnessPropose:
    """Test proposal generation."""

    def test_propose(self, self_harness_loop):
        proposals = self_harness_loop.propose()
        assert isinstance(proposals, list)

    def test_proposal_format(self, self_harness_loop):
        proposals = self_harness_loop.propose()
        for p in proposals:
            assert hasattr(p, "proposal_id")
            assert hasattr(p, "changes")


class TestSelfHarnessDecide:
    """Test decision logic."""

    def test_decide_high_score(self, self_harness_loop):
        proposal = HarnessProposal(proposal_id="p1", parent_version="0.1.0", changes={})
        metrics = {"score": 0.99, "total_cost_usd": 1.0, "surface_scores": {}}
        decision = self_harness_loop.decide(proposal, metrics)
        assert isinstance(decision, str)

    def test_decide_low_score(self, self_harness_loop):
        proposal = HarnessProposal(proposal_id="p1", parent_version="0.1.0", changes={})
        metrics = {"score": 0.1, "total_cost_usd": 1.0, "surface_scores": {}}
        decision = self_harness_loop.decide(proposal, metrics)
        assert isinstance(decision, str)


class TestSelfHarnessThresholds:
    """Test threshold configuration."""

    def test_set_thresholds(self, self_harness_loop):
        self_harness_loop.auto_accept_threshold = 0.9
        self_harness_loop.auto_reject_threshold = 0.4
        assert self_harness_loop.auto_accept_threshold == 0.9
        assert self_harness_loop.auto_reject_threshold == 0.4
