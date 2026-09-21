"""Versioned qualitative scoring rubrics for the reflex layer.

A :class:`Rubric` is an editable harness artifact: it captures the
natural-language criteria, keyword scoring signals, and escalation
thresholds used by the System 1 score primitive.  A
:class:`RubricRegistry` keeps every version of a rubric so lineage is
preserved when rubrics evolve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = ["Rubric", "RubricRegistry"]

#: Maximum number of natural-language criteria a rubric may carry.
MAX_CRITERIA = 11


@dataclass
class Rubric:
    """A versioned qualitative scoring rubric — an editable harness artifact.

    Attributes:
        rubric_id: Unique identifier for this rubric version.
        name: Human-readable rubric name (shared across versions).
        version: Semantic version string of this rubric.
        criteria: Natural-language scoring criteria (maximum 11).
        keyword_signals: Mapping of signal substring to score delta, used
            by deterministic (mock) scoring backends.
        escalation_threshold: Score at or above this escalates to System 2.
        pass_threshold: Score strictly below this passes outright.
        parent_version: Lineage pointer to the previous rubric version.
        motivation: Why this rubric exists or was changed.
        required_signals: Substrings that must ALL be present in the input
            for ``keyword_signals`` scoring to apply (rubric schema v2).
            An empty list preserves the v1 behaviour (no gating).
    """

    rubric_id: str
    name: str
    version: str = "1.0.0"
    criteria: List[str] = field(default_factory=list)
    keyword_signals: Dict[str, float] = field(default_factory=dict)
    escalation_threshold: float = 7.0
    pass_threshold: float = 2.0
    parent_version: str = ""
    motivation: str = ""
    required_signals: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate criteria count and normalise mutable defaults."""
        if not self.rubric_id or not isinstance(self.rubric_id, str):
            raise ValueError("Rubric rubric_id must be a non-empty string")
        if not self.name or not isinstance(self.name, str):
            raise ValueError("Rubric name must be a non-empty string")
        if self.criteria is None:
            self.criteria = []
        if self.keyword_signals is None:
            self.keyword_signals = {}
        if self.required_signals is None:
            self.required_signals = []
        self.required_signals = [str(signal) for signal in self.required_signals]
        if len(self.criteria) > MAX_CRITERIA:
            raise ValueError(
                f"Rubric '{self.name}' has {len(self.criteria)} criteria; "
                f"the maximum is {MAX_CRITERIA}"
            )
        self.escalation_threshold = float(self.escalation_threshold)
        self.pass_threshold = float(self.pass_threshold)
        self.keyword_signals = {
            str(signal): float(delta)
            for signal, delta in self.keyword_signals.items()
        }


class RubricRegistry:
    """Versioned store of rubrics.

    * :meth:`register` appends a new version of a rubric.
    * :meth:`get` returns the latest registered version for a name.
    * :meth:`history` returns every version, oldest first.
    * :meth:`propose_update` derives a new version from the latest one,
      recording lineage via ``parent_version``.
    """

    def __init__(self) -> None:
        self._rubrics: Dict[str, List[Rubric]] = {}

    def register(self, rubric: Rubric) -> Rubric:
        """Append *rubric* as the latest version under its name."""
        if not isinstance(rubric, Rubric):
            raise TypeError(f"Expected Rubric, got {type(rubric).__name__}")
        self._rubrics.setdefault(rubric.name, []).append(rubric)
        return rubric

    def get(self, name: str) -> Rubric:
        """Return the latest registered version of the rubric named *name*.

        Raises:
            KeyError: If no rubric with that name has been registered.
        """
        versions = self._rubrics.get(name)
        if not versions:
            raise KeyError(f"No rubric registered under name {name!r}")
        return versions[-1]

    def history(self, name: str) -> List[Rubric]:
        """Return all registered versions of *name*, oldest first.

        Raises:
            KeyError: If no rubric with that name has been registered.
        """
        versions = self._rubrics.get(name)
        if not versions:
            raise KeyError(f"No rubric registered under name {name!r}")
        return list(versions)

    def names(self) -> List[str]:
        """Return the names of all registered rubrics."""
        return list(self._rubrics)

    def propose_update(
        self,
        name: str,
        motivation: str,
        version: Optional[str] = None,
        rubric_id: Optional[str] = None,
        **changes: Any,
    ) -> Rubric:
        """Create and register a new rubric version derived from the latest.

        Args:
            name: Name of the rubric to update.
            motivation: Why the rubric is being changed (stored on the new
                version).
            version: Explicit version string for the new rubric.  When
                omitted, the patch component of the current version is
                incremented.
            rubric_id: Explicit id for the new rubric.  When omitted, the
                current id is suffixed with the new version.
            **changes: Field overrides applied to the derived rubric
                (e.g. ``keyword_signals={...}``).

        Returns:
            The newly registered :class:`Rubric`, with ``parent_version``
            pointing at the version it was derived from.

        Raises:
            KeyError: If no rubric with that name has been registered.
            ValueError: If an unknown field name is passed in *changes*.
        """
        current = self.get(name)
        new_version = version or self._bump_version(current.version)
        fields: Dict[str, Any] = {
            "rubric_id": rubric_id or f"{current.rubric_id}-v{new_version}",
            "name": current.name,
            "version": new_version,
            "criteria": list(current.criteria),
            "keyword_signals": dict(current.keyword_signals),
            "escalation_threshold": current.escalation_threshold,
            "pass_threshold": current.pass_threshold,
            "parent_version": current.version,
            "motivation": motivation,
            "required_signals": list(current.required_signals),
        }
        for key, value in changes.items():
            if key not in fields:
                raise ValueError(f"Unknown Rubric field {key!r}")
            fields[key] = value
        updated = Rubric(**fields)
        return self.register(updated)

    @staticmethod
    def _bump_version(version: str) -> str:
        """Increment the final numeric component of a version string."""
        parts = str(version).split(".")
        try:
            parts[-1] = str(int(parts[-1]) + 1)
        except (ValueError, IndexError):
            parts.append("1")
        return ".".join(parts)
