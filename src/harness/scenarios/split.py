"""Deterministic train / held-out splitting for scenarios."""

from __future__ import annotations

import random
from typing import Dict, List, Set

from harness.scenarios.base import Scenario


class ScenarioSplit:
    """Splits a list of :class:`Scenario` objects into *held-in* and
    *held-out* subsets using a deterministic random shuffle.

    Attributes:
        held_in:  Scenarios used for training / optimisation.
        held_out: Scenarios reserved for final validation.
    """

    def __init__(
        self,
        scenarios: List[Scenario],
        held_out_ratio: float = 0.2,
        seed: int = 42,
    ) -> None:
        if not scenarios:
            self._held_in: List[Scenario] = []
            self._held_out: List[Scenario] = []
            return

        total = len(scenarios)
        held_out_count = max(0, min(total, int(round(total * held_out_ratio))))

        rng = random.Random(seed)
        shuffled = list(scenarios)
        rng.shuffle(shuffled)

        self._held_out = shuffled[:held_out_count]
        self._held_in = shuffled[held_out_count:]

    @property
    def held_in(self) -> List[Scenario]:
        """Scenarios in the held-in (training) split."""
        return self._held_in

    @property
    def held_out(self) -> List[Scenario]:
        """Scenarios in the held-out (validation) split."""
        return self._held_out

    def get_surface_coverage(self, split: str = "held_in") -> Dict[str, int]:
        """Count how many scenarios touch each surface in the given *split*.

        Args:
            split: Either ``"held_in"`` or ``"held_out"``.

        Returns:
            Mapping from surface name to occurrence count.
        """
        scenarios = self._select_split(split)
        coverage: Dict[str, int] = {}
        for scenario in scenarios:
            for surface in scenario.surfaces:
                coverage[surface] = coverage.get(surface, 0) + 1
        return coverage

    def get_relationship_matrix(self) -> Dict[str, Dict[str, float]]:
        """Compute Jaccard similarity between surface pairs across scenarios.

        For each pair of surfaces, the similarity is the size of the
        intersection of scenarios that use both surfaces divided by the
        size of the union.

        Returns:
            Nested dict ``{surface_a: {surface_b: jaccard_score}}``.
        """
        surface_to_scenarios: Dict[str, Set[int]] = {}
        all_scenarios = self._held_in + self._held_out

        for idx, scenario in enumerate(all_scenarios):
            for surface in scenario.surfaces:
                surface_to_scenarios.setdefault(surface, set()).add(idx)

        surfaces = sorted(surface_to_scenarios.keys())
        matrix: Dict[str, Dict[str, float]] = {s: {} for s in surfaces}

        for i, s1 in enumerate(surfaces):
            set1 = surface_to_scenarios[s1]
            matrix[s1][s1] = 1.0
            for s2 in surfaces[i + 1 :]:
                set2 = surface_to_scenarios[s2]
                intersection = len(set1 & set2)
                union = len(set1 | set2)
                similarity = intersection / union if union > 0 else 0.0
                matrix[s1][s2] = similarity
                matrix[s2][s1] = similarity

        return matrix

    def _select_split(self, split: str) -> List[Scenario]:
        """Return the scenario list for the named split."""
        if split == "held_in":
            return self._held_in
        if split == "held_out":
            return self._held_out
        raise ValueError(f"split must be 'held_in' or 'held_out', got {split!r}")
