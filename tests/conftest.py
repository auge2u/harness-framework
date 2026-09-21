"""Shared pytest fixtures for the Harness test suite."""
from __future__ import annotations

import pytest

from harness.core.config import HarnessConfig
from harness.core.registry import PluginRegistry
from harness.core.types import Surface, SurfaceType
from harness.analysis.clusterer import FailureClusterer
from harness.verifiers.builtin import ExactVerifier, FuzzyVerifier


@pytest.fixture
def sample_surfaces():
    """A representative set of surfaces for testing."""
    return [
        Surface(name="cost_api", type=SurfaceType.API, risk_score=0.8, dependencies=["database"]),
        Surface(name="analytics_engine", type=SurfaceType.SERVICE, risk_score=0.6, dependencies=["cost_api", "database"]),
        Surface(name="alert_manager", type=SurfaceType.SERVICE, risk_score=0.4, dependencies=["analytics_engine"]),
        Surface(name="report_ui", type=SurfaceType.UI, risk_score=0.3, dependencies=["analytics_engine"]),
        Surface(name="auth_service", type=SurfaceType.SERVICE, risk_score=0.9, dependencies=[]),
        Surface(name="database", type=SurfaceType.DATABASE, risk_score=0.7, dependencies=[]),
    ]


@pytest.fixture
def sample_config(sample_surfaces):
    """A valid HarnessConfig for testing."""
    return HarnessConfig(
        version="0.1.0",
        name="test_harness",
        surfaces=sample_surfaces,
        scenarios={"scenario_a": {"difficulty": 0.5}, "scenario_b": {"difficulty": 0.7}},
        verifiers=[{"name": "exact", "type": "ExactVerifier"}],
    )


@pytest.fixture
def registry():
    """A PluginRegistry with built-in verifiers registered."""
    reg = PluginRegistry()
    reg.register_verifier("exact", ExactVerifier)
    reg.register_verifier("fuzzy", FuzzyVerifier)
    return reg


@pytest.fixture
def clusterer():
    """A fresh FailureClusterer instance."""
    return FailureClusterer()
