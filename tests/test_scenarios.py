"""Tests for harness.scenarios module."""
from __future__ import annotations

import pytest

from harness.scenarios.base import Scenario
from harness.scenarios.split import ScenarioSplit


class DummyScenario(Scenario):
    """Minimal concrete scenario for testing."""
    scenario_id = "dummy_1"
    name = "Dummy"
    description = "A dummy scenario"
    tags = ["test"]
    surfaces = ["cost_api", "database"]
    difficulty = 0.5

    def setup(self):
        return {"initialized": True}

    def run(self, agent, context):
        return {"output": "done"}

    def teardown(self, context):
        pass


class AnotherScenario(Scenario):
    """Another scenario with different surfaces."""
    scenario_id = "another_1"
    name = "Another"
    description = "Another scenario"
    tags = ["test"]
    surfaces = ["analytics_engine", "cost_api"]
    difficulty = 0.7

    def setup(self):
        return {}

    def run(self, agent, context):
        return {"output": "done"}

    def teardown(self, context):
        pass


class TestScenarioBase:
    """Test Scenario ABC."""

    def test_concrete_scenario(self):
        s = DummyScenario()
        assert s.scenario_id == "dummy_1"
        assert s.surfaces == ["cost_api", "database"]

    def test_get_expected_default_none(self):
        s = DummyScenario()
        assert s.get_expected() is None

    def test_setup_run_teardown(self):
        s = DummyScenario()
        ctx = s.setup()
        assert ctx["initialized"] is True
        result = s.run(None, ctx)
        assert result["output"] == "done"
        s.teardown(ctx)


class TestScenarioSplit:
    """Test deterministic scenario splitting."""

    def test_empty_scenarios(self):
        split = ScenarioSplit([], held_out_ratio=0.2)
        assert split.held_in == []
        assert split.held_out == []

    def test_all_held_in_when_ratio_zero(self):
        scenarios = [DummyScenario(), AnotherScenario()]
        split = ScenarioSplit(scenarios, held_out_ratio=0.0)
        assert len(split.held_in) == 2
        assert len(split.held_out) == 0

    def test_deterministic_with_seed(self):
        scenarios = [DummyScenario(), AnotherScenario()]
        split1 = ScenarioSplit(scenarios, held_out_ratio=0.5, seed=42)
        split2 = ScenarioSplit(scenarios, held_out_ratio=0.5, seed=42)
        ids1 = [s.scenario_id for s in split1.held_in]
        ids2 = [s.scenario_id for s in split2.held_in]
        assert ids1 == ids2

    def test_surface_coverage(self):
        scenarios = [DummyScenario(), AnotherScenario()]
        split = ScenarioSplit(scenarios, held_out_ratio=0.0)
        coverage = split.get_surface_coverage("held_in")
        assert coverage["cost_api"] == 2
        assert coverage["database"] == 1

    def test_surface_coverage_invalid_split(self):
        scenarios = [DummyScenario()]
        split = ScenarioSplit(scenarios, held_out_ratio=0.0)
        with pytest.raises(ValueError):
            split.get_surface_coverage("invalid")

    def test_relationship_matrix(self):
        scenarios = [DummyScenario(), AnotherScenario()]
        split = ScenarioSplit(scenarios, held_out_ratio=0.0)
        matrix = split.get_relationship_matrix()
        assert matrix["cost_api"]["cost_api"] == 1.0
        assert "database" in matrix
        assert "analytics_engine" in matrix
