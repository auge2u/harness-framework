"""Harness configuration dataclass with validation and mutation helpers."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from harness.core.exceptions import ConfigValidationError
from harness.core.types import ChangeScope, Surface, SurfaceType


@dataclass
class HarnessConfig:
    """Top-level configuration for a Harness test suite."""

    version: str
    name: str
    surfaces: List[Surface]
    scenarios: Dict[str, Any]
    verifiers: List[Dict[str, Any]]

    held_out_ratio: float = 0.2
    circuit_parallelism: int = 4
    cost_budget_usd: float = 10.0
    auto_accept_threshold: float = 0.95
    auto_reject_threshold: float = 0.5
    max_proposals_per_cycle: int = 10
    lineage_enabled: bool = True
    telemetry_enabled: bool = True

    def __post_init__(self):
        """Normalize mutable defaults and coerce types after construction."""
        if self.surfaces is None:
            self.surfaces = []
        if self.scenarios is None:
            self.scenarios = {}
        if self.verifiers is None:
            self.verifiers = []
        self.held_out_ratio = float(self.held_out_ratio)
        self.circuit_parallelism = int(self.circuit_parallelism)
        self.cost_budget_usd = float(self.cost_budget_usd)
        self.auto_accept_threshold = float(self.auto_accept_threshold)
        self.auto_reject_threshold = float(self.auto_reject_threshold)
        self.max_proposals_per_cycle = int(self.max_proposals_per_cycle)
        self.lineage_enabled = bool(self.lineage_enabled)
        self.telemetry_enabled = bool(self.telemetry_enabled)

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _surface_to_dict(surface: Surface) -> Dict[str, Any]:
        """Convert a :class:`Surface` instance to a plain dictionary."""
        return {
            "name": surface.name,
            "type": surface.type.name if isinstance(surface.type, SurfaceType) else str(surface.type),
            "schema": surface.schema,
            "dependencies": surface.dependencies,
            "risk_score": surface.risk_score,
        }

    @staticmethod
    def _surface_from_dict(data: Dict[str, Any]) -> Surface:
        """Build a :class:`Surface` from a plain dictionary (tolerant)."""
        if isinstance(data, Surface):
            return data
        raw_type = data.get("type", "API")
        if isinstance(raw_type, str):
            try:
                surface_type = SurfaceType[raw_type.upper()]
            except KeyError:
                surface_type = SurfaceType.API
        elif isinstance(raw_type, SurfaceType):
            surface_type = raw_type
        else:
            surface_type = SurfaceType.API
        return Surface(
            name=str(data.get("name", "")),
            type=surface_type,
            schema=data.get("schema", {}) or {},
            dependencies=list(data.get("dependencies", []) or []),
            risk_score=float(data.get("risk_score", 0.0)),
        )

    @classmethod
    def from_yaml(cls, path: str) -> HarnessConfig:
        """Load a configuration from a YAML file.

        Args:
            path: Filesystem path to the YAML file.

        Returns:
            A fully populated :class:`HarnessConfig`.

        Raises:
            ConfigValidationError: If the file cannot be read or parsed.
        """
        try:
            import yaml
        except ImportError:
            raise ConfigValidationError(
                "PyYAML is required for YAML loading. Install it with: pip install pyyaml"
            )
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except FileNotFoundError as exc:
            raise ConfigValidationError(f"Configuration file not found: {path}") from exc
        except Exception as exc:
            raise ConfigValidationError(f"Failed to parse YAML from {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigValidationError(f"YAML root must be a mapping, got {type(data).__name__}")
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> HarnessConfig:
        """Construct a :class:`HarnessConfig` from a plain dictionary.

        Handles automatic conversion of surface dictionaries to :class:`Surface`
        objects, expansion of shorthand scenario definitions, and normalisation
        of the verifier list.
        """
        data = dict(data)  # shallow copy so we can pop safely

        # --- surfaces --------------------------------------------------
        raw_surfaces = data.pop("surfaces", [])
        surfaces: List[Surface] = []
        if raw_surfaces is None:
            raw_surfaces = []
        for item in raw_surfaces:
            if isinstance(item, dict):
                surfaces.append(cls._surface_from_dict(item))
            elif isinstance(item, Surface):
                surfaces.append(item)
            else:
                raise ConfigValidationError(
                    f"Each surface must be a dict or Surface, got {type(item).__name__}"
                )

        # --- scenarios -------------------------------------------------
        raw_scenarios = data.pop("scenarios", {})
        if raw_scenarios is None:
            raw_scenarios = {}
        scenarios: Dict[str, Any] = {}
        for key, value in raw_scenarios.items():
            if isinstance(value, dict):
                scenarios[key] = value
            else:
                scenarios[key] = {"_raw": value}

        # --- verifiers -------------------------------------------------
        raw_verifiers = data.pop("verifiers", [])
        if raw_verifiers is None:
            raw_verifiers = []
        verifiers: List[Dict[str, Any]] = []
        for item in raw_verifiers:
            if isinstance(item, dict):
                verifiers.append(dict(item))
            elif isinstance(item, str):
                verifiers.append({"type": item})
            else:
                raise ConfigValidationError(
                    f"Each verifier must be a dict or str, got {type(item).__name__}"
                )

        kwargs: Dict[str, Any] = {
            "version": data.pop("version", "0.1.0"),
            "name": data.pop("name", "harness"),
            "surfaces": surfaces,
            "scenarios": scenarios,
            "verifiers": verifiers,
            "held_out_ratio": data.pop("held_out_ratio", 0.2),
            "circuit_parallelism": data.pop("circuit_parallelism", 4),
            "cost_budget_usd": data.pop("cost_budget_usd", 10.0),
            "auto_accept_threshold": data.pop("auto_accept_threshold", 0.95),
            "auto_reject_threshold": data.pop("auto_reject_threshold", 0.5),
            "max_proposals_per_cycle": data.pop("max_proposals_per_cycle", 10),
            "lineage_enabled": data.pop("lineage_enabled", True),
            "telemetry_enabled": data.pop("telemetry_enabled", True),
        }
        return cls(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this configuration to a plain dictionary.

        Enums are rendered as strings for JSON/YAML compatibility.
        """
        return {
            "version": self.version,
            "name": self.name,
            "surfaces": [self._surface_to_dict(s) for s in self.surfaces],
            "scenarios": copy.deepcopy(self.scenarios),
            "verifiers": copy.deepcopy(self.verifiers),
            "held_out_ratio": self.held_out_ratio,
            "circuit_parallelism": self.circuit_parallelism,
            "cost_budget_usd": self.cost_budget_usd,
            "auto_accept_threshold": self.auto_accept_threshold,
            "auto_reject_threshold": self.auto_reject_threshold,
            "max_proposals_per_cycle": self.max_proposals_per_cycle,
            "lineage_enabled": self.lineage_enabled,
            "telemetry_enabled": self.telemetry_enabled,
        }

    def to_yaml(self, path: Optional[str] = None) -> str:
        """Serialize this config to YAML.

        Args:
            path: If provided, write YAML to this file path.

        Returns:
            The YAML string representation.
        """
        import yaml
        data = self.to_dict()
        yaml_str = yaml.dump(
            data,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(yaml_str)
        return yaml_str

    def validate_detailed(self) -> Dict[str, Any]:
        """Validate config and return detailed results.

        Returns:
            Dict with "valid" (bool), "errors" (list), "warnings" (list),
            "surface_count", "verifier_count", "scenario_count",
            "risk_assessment" (dict of surface -> risk_score).
        """
        errors = self.validate()
        warnings: List[str] = []

        # Add warnings for edge cases
        if self.held_out_ratio > 0.5:
            warnings.append(
                "held_out_ratio > 0.5 may leave too few training scenarios"
            )
        if self.cost_budget_usd < 1.0:
            warnings.append(
                "cost_budget_usd < $1.0 may be insufficient for meaningful evaluation"
            )
        if not self.surfaces:
            warnings.append(
                "No surfaces declared — harness has nothing to evaluate"
            )
        if len(self.verifiers) == 0:
            warnings.append(
                "No verifiers configured — all scenarios will pass by default"
            )

        risk_assessment = {
            s.name: s.risk_score for s in self.surfaces
        }

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "surface_count": len(self.surfaces),
            "verifier_count": len(self.verifiers),
            "scenario_count": len(self.scenarios),
            "risk_assessment": risk_assessment,
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> List[str]:
        """Validate the configuration and return a list of error messages.

        An empty list means the configuration is valid.
        """
        errors: List[str] = []

        if not self.surfaces:
            errors.append("At least one surface must be defined.")

        if not (0.0 <= self.held_out_ratio <= 1.0):
            errors.append(
                f"held_out_ratio must be in [0, 1], got {self.held_out_ratio}"
            )

        if not (0.0 <= self.auto_accept_threshold <= 1.0):
            errors.append(
                f"auto_accept_threshold must be in [0, 1], got {self.auto_accept_threshold}"
            )
        if not (0.0 <= self.auto_reject_threshold <= 1.0):
            errors.append(
                f"auto_reject_threshold must be in [0, 1], got {self.auto_reject_threshold}"
            )
        if self.auto_accept_threshold <= self.auto_reject_threshold:
            errors.append(
                f"auto_accept_threshold ({self.auto_accept_threshold}) must be greater "
                f"than auto_reject_threshold ({self.auto_reject_threshold})"
            )

        if self.circuit_parallelism < 1:
            errors.append(
                f"circuit_parallelism must be >= 1, got {self.circuit_parallelism}"
            )

        if self.cost_budget_usd <= 0:
            errors.append(
                f"cost_budget_usd must be > 0, got {self.cost_budget_usd}"
            )

        seen: Set[str] = set()
        for surface in self.surfaces:
            if surface.name in seen:
                errors.append(f"Duplicate surface name: {surface.name}")
            seen.add(surface.name)

        valid_names = {s.name for s in self.surfaces}
        for surface in self.surfaces:
            for dep in surface.dependencies:
                if dep not in valid_names:
                    errors.append(
                        f"Surface '{surface.name}' has dangling dependency: '{dep}'"
                    )

        return errors

    # ------------------------------------------------------------------
    # Dependency graph helpers
    # ------------------------------------------------------------------

    def get_surface_matrix(self) -> Dict[str, List[str]]:
        """Return a mapping from each surface name to the list of surfaces
        that depend on it (i.e. surfaces that declare it as a dependency)."""
        result: Dict[str, List[str]] = {s.name: [] for s in self.surfaces}
        for surface in self.surfaces:
            for dep in surface.dependencies:
                if dep in result:
                    result[dep].append(surface.name)
        return result

    def _build_dep_graph(self) -> Dict[str, Set[str]]:
        """Build an adjacency list for the undirected dependency graph."""
        nodes = {s.name for s in self.surfaces}
        graph: Dict[str, Set[str]] = {n: set() for n in nodes}
        for surface in self.surfaces:
            for dep in surface.dependencies:
                if dep in graph:
                    graph[surface.name].add(dep)
                    graph[dep].add(surface.name)
        return graph

    def get_scope_for_change(self, changed_surfaces: List[str]) -> ChangeScope:
        """Determine the :class:`ChangeScope` for a set of changed surfaces.

        * ``ATOMIC``  — exactly one surface changed.
        * ``COMPONENT`` — all changed surfaces are pairwise connected in the
          dependency graph (directly or transitively).
        * ``SYSTEM`` — otherwise (changes span disconnected components).
        """
        if not changed_surfaces:
            return ChangeScope.SYSTEM

        unique = list(dict.fromkeys(changed_surfaces))
        if len(unique) == 1:
            return ChangeScope.ATOMIC

        graph = self._build_dep_graph()
        valid = {s.name for s in self.surfaces}
        unique = [s for s in unique if s in valid]
        if len(unique) <= 1:
            return ChangeScope.ATOMIC

        start = unique[0]
        visited: Set[str] = set()
        stack = [start]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            for neighbor in graph.get(node, set()):
                if neighbor not in visited:
                    stack.append(neighbor)

        if all(s in visited for s in unique):
            return ChangeScope.COMPONENT
        return ChangeScope.SYSTEM

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def apply_changes(self, changes: Dict[str, Any]) -> HarnessConfig:
        """Return a **new** :class:`HarnessConfig` with *changes* applied.

        Supported change directives:

        * ``add_surfaces`` — list of surface dicts or :class:`Surface` objects
          to append.
        * ``remove_surfaces`` — list of surface names to remove.
        * ``tune_verifier`` — dict with ``index`` (int) and ``config`` (dict)
          to deep-merge into the verifier at that index.
        * ``set_thresholds`` — dict with ``auto_accept`` and/or ``auto_reject``.
        """
        new_config = copy.deepcopy(self)

        if "add_surfaces" in changes:
            for item in changes["add_surfaces"]:
                if isinstance(item, dict):
                    new_config.surfaces.append(self._surface_from_dict(item))
                elif isinstance(item, Surface):
                    new_config.surfaces.append(copy.deepcopy(item))
                else:
                    raise ConfigValidationError(
                        f"add_surfaces items must be dict or Surface, got {type(item).__name__}"
                    )

        if "remove_surfaces" in changes:
            remove_names = set(changes["remove_surfaces"])
            new_config.surfaces = [
                s for s in new_config.surfaces if s.name not in remove_names
            ]

        if "tune_verifier" in changes:
            tune = changes["tune_verifier"]
            idx = tune.get("index", 0)
            if 0 <= idx < len(new_config.verifiers):
                new_config.verifiers[idx] = _deep_merge(
                    new_config.verifiers[idx], tune.get("config", {})
                )
            else:
                raise ConfigValidationError(
                    f"Verifier index {idx} out of range (0-{len(new_config.verifiers) - 1})"
                )

        if "set_thresholds" in changes:
            thresholds = changes["set_thresholds"]
            if "auto_accept" in thresholds:
                new_config.auto_accept_threshold = float(thresholds["auto_accept"])
            if "auto_reject" in thresholds:
                new_config.auto_reject_threshold = float(thresholds["auto_reject"])

        return new_config


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge *overlay* into *base* (mutates and returns *base*)."""
    for key, value in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            base[key] = _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base
