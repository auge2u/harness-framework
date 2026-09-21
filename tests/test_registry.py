"""Tests for harness.core.registry module."""
from __future__ import annotations

import pytest

from harness.core.registry import PluginRegistry
from harness.verifiers.builtin import ExactVerifier, FuzzyVerifier


class TestVerifierRegistration:
    """Test verifier registration and lookup."""

    def test_register_and_get(self, registry):
        verifier_cls = registry.get_verifier("exact")
        assert verifier_cls is ExactVerifier

    def test_get_missing_raises(self, registry):
        with pytest.raises(KeyError):
            registry.get_verifier("nonexistent")

    def test_list_verifiers(self, registry):
        verifiers = registry.list_verifiers()
        assert "exact" in verifiers
        assert "fuzzy" in verifiers


class TestScenarioRegistration:
    """Test scenario registration and lookup."""

    def test_register_scenario(self, registry):
        registry.register_scenario("test_scenario", {"key": "value"})
        scenario = registry.get_scenario("test_scenario")
        assert scenario == {"key": "value"}

    def test_get_missing_scenario_raises(self, registry):
        with pytest.raises(KeyError):
            registry.get_scenario("nonexistent")

    def test_list_scenarios(self, registry):
        registry.register_scenario("s1", {})
        registry.register_scenario("s2", {})
        scenarios = registry.list_scenarios()
        assert "s1" in scenarios
        assert "s2" in scenarios


class TestSurfaceRegistration:
    """Test surface registration and lookup."""

    def test_register_surface(self, registry):
        registry.register_surface("test_surface", {"type": "API"})
        surface = registry.get_surface("test_surface")
        assert surface == {"type": "API"}

    def test_list_surfaces(self, registry):
        registry.register_surface("surf1", {})
        surfaces = registry.list_surfaces()
        assert "surf1" in surfaces


class TestDuplicateRegistration:
    """Test duplicate registration behavior."""

    def test_duplicate_verifier_raises(self, registry):
        with pytest.raises(ValueError):
            registry.register_verifier("exact", ExactVerifier)

    def test_duplicate_scenario_raises(self, registry):
        registry.register_scenario("dup", {})
        with pytest.raises(ValueError):
            registry.register_scenario("dup", {})

    def test_override_flag(self, registry):
        registry.register_verifier("exact", FuzzyVerifier, override=True)
        assert registry.get_verifier("exact") is FuzzyVerifier
