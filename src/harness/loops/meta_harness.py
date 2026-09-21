"""MetaHarness — pluggable proposal generation and orchestration.

The :class:`MetaHarnessOrchestrator` coordinates multiple *proposer*
plugins, each of which can generate :class:`HarnessProposal` objects
based on the current state of the harness.  Individual proposer failures
are isolated so that one broken plugin does not crash the orchestration.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from harness.core.types import HarnessProposal, FailureSignature
from harness.core.config import HarnessConfig
from harness.core.registry import PluginRegistry

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MetaHarnessProposer (ABC)
# ---------------------------------------------------------------------------

class MetaHarnessProposer(ABC):
    """Abstract base class for harness proposal generators.

    A *proposer* analyses the current harness state (failures, coverage,
    performance) and produces a list of :class:`HarnessProposal` objects
    that the orchestrator can evaluate.
    """

    @abstractmethod
    def propose(
        self,
        current_config: HarnessConfig,
        failure_clusters: List[FailureSignature],
        surface_matrix: Dict[str, Dict[str, float]],
        recent_traces: List[Any],
    ) -> List[HarnessProposal]:
        """Generate proposals based on the current harness state.

        Parameters
        ----------
        current_config:
            The active harness configuration.
        failure_clusters:
            Failure signatures from the clusterer.
        surface_matrix:
            Co-occurrence matrix from
            :meth:`FailureClusterer.get_relationship_impact`.
        recent_traces:
            The most recent trace records (raw, not yet clustered).

        Returns
        -------
        list[HarnessProposal]
        """
        ...

    @abstractmethod
    def get_capabilities(self) -> Dict[str, Any]:
        """Return a descriptor of what this proposer can do.

        The returned dictionary should contain at least a ``"name"`` key
        and may include ``"description"``, ``"supported_scopes"``,
        ``"version"``, etc.

        Returns
        -------
        dict
        """
        ...


# ---------------------------------------------------------------------------
# MetaHarnessOrchestrator
# ---------------------------------------------------------------------------

class MetaHarnessOrchestrator:
    """Orchestrates multiple :class:`MetaHarnessProposer` plugins.

    The orchestrator:

    1. Maintains a registry of named proposers.
    2. Dispatches ``request_proposals`` to selected proposers.
    3. Aggregates proposals, catching and logging individual failures.
    4. Tracks per-proposer acceptance rates for ranking.

    Parameters
    ----------
    registry:
        The global plugin registry (used to look up additional resources).
    """

    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry
        self._proposers: Dict[str, MetaHarnessProposer] = {}
        self._scores: Dict[str, float] = {}

    # -- Registration ---------------------------------------------------------

    def register_proposer(self, name: str, proposer: MetaHarnessProposer) -> None:
        """Register a proposer under *name*.

        If *name* already exists, the previous proposer is overwritten.
        """
        self._proposers[name] = proposer
        if name not in self._scores:
            self._scores[name] = 0.5  # neutral initial score

    def unregister_proposer(self, name: str) -> bool:
        """Remove the proposer registered under *name*.

        Returns ``True`` if a proposer was removed, ``False`` otherwise.
        """
        if name in self._proposers:
            del self._proposers[name]
            return True
        return False

    def list_proposers(self) -> Dict[str, Dict[str, Any]]:
        """Return a mapping of proposer name → capability descriptor."""
        result: Dict[str, Dict[str, Any]] = {}
        for name, proposer in self._proposers.items():
            try:
                result[name] = proposer.get_capabilities()
            except Exception as exc:
                logger.warning(
                    "Proposer %s failed get_capabilities(): %s", name, exc
                )
                result[name] = {"name": name, "error": str(exc)}
        return result

    # -- Proposal dispatch ----------------------------------------------------

    def request_proposals(
        self,
        context: Dict[str, Any],
        selector: Optional[str] = None,
    ) -> List[HarnessProposal]:
        """Request proposals from registered proposers.

        Parameters
        ----------
        context:
            A dictionary containing the current harness state.  Expected keys:
            * ``current_config`` — :class:`HarnessConfig`
            * ``failure_clusters`` — list of :class:`FailureSignature`
            * ``surface_matrix`` — co-occurrence dict
            * ``recent_traces`` — list of recent trace records
        selector:
            If provided, only proposers whose name contains *selector* (case-
            insensitive) are invoked.  If ``None``, all proposers run.

        Returns
        -------
        list[HarnessProposal]
            All proposals from all invoked proposers, concatenated in
            registration order.

        Notes
        -----
        Individual proposer exceptions are caught, logged, and do **not**
        abort the dispatch.
        """
        current_config = context.get("current_config")
        failure_clusters = context.get("failure_clusters", [])
        surface_matrix = context.get("surface_matrix", {})
        recent_traces = context.get("recent_traces", [])

        if current_config is None:
            raise ValueError("context['current_config'] is required")

        proposals: List[HarnessProposal] = []

        for name, proposer in self._proposers.items():
            if selector is not None and selector.lower() not in name.lower():
                continue

            try:
                batch = proposer.propose(
                    current_config=current_config,
                    failure_clusters=failure_clusters,
                    surface_matrix=surface_matrix,
                    recent_traces=recent_traces,
                )
                if batch:
                    proposals.extend(batch)
                    logger.info(
                        "Proposer %s generated %d proposals", name, len(batch)
                    )
                else:
                    logger.debug("Proposer %s returned no proposals", name)
            except Exception as exc:
                logger.error(
                    "Proposer %s failed during propose(): %s", name, exc,
                    exc_info=True,
                )
                self._scores[name] = max(0.0, self._scores.get(name, 0.5) - 0.05)

        return proposals

    # -- Scoring --------------------------------------------------------------

    def score_proposer(self, name: str, historical_acceptance_rate: float) -> None:
        """Update the internal score for proposer *name*.

        The score is a simple exponential moving average:

        .. math::

           score_{new} = 0.7 \times score_{old} + 0.3 \times rate

        Parameters
        ----------
        name:
            Proposer name (must be registered).
        historical_acceptance_rate:
            A value in ``[0.0, 1.0]``.

        Raises
        ------
        KeyError
            If *name* is not a registered proposer.
        """
        if name not in self._proposers:
            raise KeyError(f"No proposer registered under name: {name!r}")

        old_score = self._scores.get(name, 0.5)
        rate = max(0.0, min(1.0, historical_acceptance_rate))
        self._scores[name] = 0.7 * old_score + 0.3 * rate

    def get_proposer_scores(self) -> Dict[str, float]:
        """Return the current score for every registered proposer."""
        return dict(self._scores)

    def rank_proposers(self) -> List[tuple[str, float]]:
        """Return proposers ranked by score (highest first)."""
        return sorted(
            self._scores.items(),
            key=lambda x: x[1],
            reverse=True,
        )
