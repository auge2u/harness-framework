"""Tests for the harness.config module."""
from __future__ import annotations

import json
import pytest

from harness.core.config import HarnessConfig, MAX_SURFACES
from harness.core.types import Surface, SurfaceType


# ---------------------------------------------------------------------------
# Sample config data
# ---------------------------------------------------------------------------

SAMPLE_SURFACES = [
    {"name": "cost_api", "type": "API", "risk_score": 0.8, "dependencies": ["database"]},
    {"name": "analytics_engine", "type": "SERVICE", "risk_score": 0.6, "dependencies": ["cost_api"]},
    {"name": "alert_manager", "type": "SERVICE", "risk_score": 0.4, "dependencies": ["analytics_engine"]},
    {"name": "report_ui", "type": "UI", "risk_score": 0.3, "dependencies": ["analytics_engine"]},
    {"name": "auth_service", "type": "SERVICE", "risk_score": 0.9, "dependencies": []},
    {"name": "database", "type": "DATABASE", "risk_score": 0.7, "dependencies": []},
]


def make_config_dict(**overrides):
    """Return a valid config dictionary with optional overrides."""
    base = {
        "version": "0.1.0",
        "name": "test_harness",
        "surfaces": SAMPLE_SURFACES,
        "scenarios": {"scenario_a": {}, "scenario_b": {}},
        "verifiers": [{"name": "exact"}],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# from_dict
# ---------------------------------------------------------------------------

class TestFromDict:
    """Test HarnessConfig.from_dict()."""

    def test_from_dict_minimal(self):
        config = HarnessConfig.from_dict({"version": "0.1.0", "name": "test"})
        assert config.version == "0.1.0"
        assert config.name == "test"
        assert config.surfaces == []

    def test_from_dict_missing_version_raises(self):
        with pytest.raises(ValueError):
            HarnessConfig.from_dict({"name": "test"})

    def test_from_dict_surfaces_parsed(self):
        config = HarnessConfig.from_dict(make_config_dict())
        assert len(config.surfaces) == 6
        assert all(isinstance(s, Surface) for s in config.surfaces)

    def test_from_dict_surface_attributes(self):
        config = HarnessConfig.from_dict(make_config_dict())
        cost_api = next(s for s in config.surfaces if s.name == "cost_api")
        assert cost_api.risk_score == 0.8
        assert cost_api.dependencies == ["database"]

    def test_from_dict_scenarios_parsed(self):
        config = HarnessConfig.from_dict(make_config_dict())
        assert "scenario_a" in config.scenarios
        assert "scenario_b" in config.scenarios

    def test_from_dict_verifiers_parsed(self):
        config = HarnessConfig.from_dict(make_config_dict())
        assert len(config.verifiers) == 1

    def test_from_dict_round_trip(self, sample_config):
        """Test to_dict -> from_dict round-trip."""
        d = sample_config.to_dict()
        restored = HarnessConfig.from_dict(d)
        assert restored.version == sample_config.version
        assert restored.name == sample_config.name
        assert len(restored.surfaces) == len(sample_config.surfaces)


# ---------------------------------------------------------------------------
# from_yaml
# ---------------------------------------------------------------------------

class TestFromYaml:
    """Test HarnessConfig.from_yaml()."""

    def test_from_yaml_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            HarnessConfig.from_yaml("/nonexistent/path/config.yaml")

    def test_from_yaml_valid(self, tmp_path, sample_config):
        yaml_path = tmp_path / "config.yaml"
        sample_config.to_yaml(str(yaml_path))
        loaded = HarnessConfig.from_yaml(str(yaml_path))
        assert loaded.version == sample_config.version
        assert loaded.name == sample_config.name

    def test_from_yaml_invalid_yaml_raises(self, tmp_path):
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("invalid: [yaml: {\n")
        with pytest.raises(ValueError):
            HarnessConfig.from_yaml(str(yaml_path))

    def test_from_yaml_missing_version_raises(self, tmp_path):
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("name: test\n")
        with pytest.raises(ValueError):
            HarnessConfig.from_yaml(str(yaml_path))


# ---------------------------------------------------------------------------
# to_yaml
# ---------------------------------------------------------------------------

class TestToYaml:
    """Test HarnessConfig.to_yaml()."""

    def test_to_yaml_creates_file(self, tmp_path, sample_config):
        yaml_path = tmp_path / "config.yaml"
        sample_config.to_yaml(str(yaml_path))
        assert yaml_path.exists()

    def test_to_yaml_round_trip(self, tmp_path, sample_config):
        yaml_path = tmp_path / "config.yaml"
        sample_config.to_yaml(str(yaml_path))
        loaded = HarnessConfig.from_yaml(str(yaml_path))
        assert loaded.name == sample_config.name
        assert len(loaded.surfaces) == len(sample_config.surfaces)

    def test_to_yaml_valid_yaml(self, tmp_path, sample_config):
        import yaml
        yaml_path = tmp_path / "config.yaml"
        sample_config.to_yaml(str(yaml_path))
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        assert data["version"] == sample_config.version


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    """Test config validation logic."""

    def test_validate_valid_config(self, sample_config):
        errors = sample_config.validate()
        assert errors == []

    def test_validate_missing_name(self):
        config = HarnessConfig.from_dict(make_config_dict(name=""))
        errors = config.validate()
        assert len(errors) > 0

    def test_validate_missing_version(self):
        config = HarnessConfig(
            version="", name="test", surfaces=[], scenarios={}, verifiers=[],
        )
        errors = config.validate()
        assert len(errors) > 0

    def test_validate_too_many_surfaces(self):
        surfaces = [
            Surface(name=f"s{i}", type=SurfaceType.API)
            for i in range(MAX_SURFACES + 1)
        ]
        config = HarnessConfig(
            version="0.1.0", name="test", surfaces=surfaces,
            scenarios={}, verifiers=[],
        )
        errors = config.validate()
        assert any("surface" in e.lower() for e in errors)

    def test_validate_dependency_cycle_detected(self):
        surfaces = [
            Surface(name="a", type=SurfaceType.API, dependencies=["b"]),
            Surface(name="b", type=SurfaceType.API, dependencies=["a"]),
        ]
        config = HarnessConfig(
            version="0.1.0", name="test", surfaces=surfaces,
            scenarios={}, verifiers=[],
        )
        errors = config.validate()
        assert len(errors) > 0

    def test_validate_duplicate_surfaces(self):
        surfaces = [
            Surface(name="dup", type=SurfaceType.API),
            Surface(name="dup", type=SurfaceType.SERVICE),
        ]
        config = HarnessConfig(
            version="0.1.0", name="test", surfaces=surfaces,
            scenarios={}, verifiers=[],
        )
        errors = config.validate()
        assert len(errors) > 0

    def test_validate_unknown_dependency(self):
        surfaces = [
            Surface(name="a", type=SurfaceType.API, dependencies=["nonexistent"]),
        ]
        config = HarnessConfig(
            version="0.1.0", name="test", surfaces=surfaces,
            scenarios={}, verifiers=[],
        )
        errors = config.validate()
        assert len(errors) > 0

    def test_validate_detailed_returns_dict(self, sample_config):
        result = sample_config.validate_detailed()
        assert isinstance(result, dict)
        assert "valid" in result
        assert "errors" in result
        assert "warnings" in result


# ---------------------------------------------------------------------------
# apply_changes
# ---------------------------------------------------------------------------

class TestApplyChanges:
    """Test HarnessConfig.apply_changes()."""

    def test_add_surface(self, sample_config):
        changes = {"add_surfaces": [{"name": "new_surf", "type": "API"}]}
        new_config = sample_config.apply_changes(changes)
        assert len(new_config.surfaces) == len(sample_config.surfaces) + 1
        assert any(s.name == "new_surf" for s in new_config.surfaces)

    def test_remove_surface(self, sample_config):
        changes = {"remove_surfaces": ["report_ui"]}
        new_config = sample_config.apply_changes(changes)
        assert len(new_config.surfaces) == len(sample_config.surfaces) - 1
        assert not any(s.name == "report_ui" for s in new_config.surfaces)

    def test_apply_changes_returns_new_instance(self, sample_config):
        new_config = sample_config.apply_changes({})
        assert new_config is not sample_config

    def test_apply_changes_immutable_original(self, sample_config):
        original_count = len(sample_config.surfaces)
        sample_config.apply_changes({"add_surfaces": [{"name": "x"}]})
        assert len(sample_config.surfaces) == original_count


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Test edge cases and defaults."""

    def test_empty_surfaces_list(self):
        config = HarnessConfig.from_dict(make_config_dict(surfaces=[]))
        assert config.surfaces == []

    def test_empty_scenarios_dict(self):
        config = HarnessConfig.from_dict(make_config_dict(scenarios={}))
        assert config.scenarios == {}

    def test_held_out_ratio_default(self, sample_config):
        assert sample_config.held_out_ratio == 0.2

    def test_held_out_ratio_custom(self):
        config = HarnessConfig.from_dict(make_config_dict(held_out_ratio=0.5))
        assert config.held_out_ratio == 0.5

    def test_cost_budget_default(self, sample_config):
        assert sample_config.cost_budget_usd == 10.0

    def test_max_surfaces_constant(self):
        assert MAX_SURFACES == 100
