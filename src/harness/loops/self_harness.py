"""SelfHarnessLoop — autonomous self-improvement cycle for the Harness framework.

The loop repeatedly:

1. *Observes* — queries the :class:`TraceStore` for recent failures.
2. *Analyses* — clusters failures with :class:`FailureClusterer`.
3. *Proposes* — generates :class:`HarnessProposal` objects.
4. *Evaluates* — runs proposals through held-in scenarios.
5. *Decides* — accepts, rejects, or defers based on configured thresholds.
6. *Commits* — records accepted configurations in :class:`HarnessLineage`.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from harness.core.types import (
    FailureSignature,
    HarnessProposal,
    TraceRecord,
    Verdict,
)
from harness.core.config import HarnessConfig
from harness.core.registry import PluginRegistry
from harness.store.trace_store import TraceStore
from harness.analysis.lineage import HarnessLineage
from harness.analysis.clusterer import FailureClusterer
from harness.runners.runner import SingleRunner
from harness.scenarios.split import ScenarioSplit

logger = logging.getLogger(__name__)


class SelfHarnessLoop:
    """Autonomous self-improvement loop.

    Parameters
    ----------
    config:
        The current (baseline) harness configuration.
    registry:
        Plugin registry for verifier lookup.
    trace_store:
        Persistent trace store.
    lineage:
        Lineage store for version history.
    clusterer:
        Failure clusterer instance.
    """

    def __init__(
        self,
        config: HarnessConfig,
        registry: PluginRegistry,
        trace_store: TraceStore,
        lineage: HarnessLineage,
        clusterer: FailureClusterer,
    ) -> None:
        self.config = config
        self.registry = registry
        self.trace_store = trace_store
        self.lineage = lineage
        self.clusterer = clusterer

        self.auto_accept_threshold: float = getattr(config, "auto_accept_threshold", 0.8)
        self.auto_reject_threshold: float = getattr(config, "auto_reject_threshold", 0.3)
        self._cycle_history: List[Dict[str, Any]] = []

    # -- Main loop ------------------------------------------------------------

    def run(
        self,
        max_cycles: int = 10,
        held_in_scenarios: Optional[List[Any]] = None,
    ) -> List[Tuple[HarnessProposal, str, Dict[str, Any]]]:
        """Run the self-improvement loop for up to *max_cycles* iterations.

        Returns
        -------
        list[tuple[HarnessProposal, str, dict]]
            Each tuple is ``(proposal, decision, metrics)`` where *decision*
            is one of ``"accept"``, ``"reject"``, or ``"review"``.
        """
        results: List[Tuple[HarnessProposal, str, Dict[str, Any]]] = []

        for cycle in range(max_cycles):
            logger.info("Self-harness cycle %d/%d", cycle + 1, max_cycles)

            proposals = self.propose()
            if not proposals:
                logger.info("No proposals generated — stopping early")
                break

            for proposal in proposals:
                metrics = self._evaluate(proposal, held_in_scenarios)
                decision = self.decide(proposal, metrics)
                results.append((proposal, decision, metrics))

                if decision == "accept":
                    self._accept(proposal, metrics)

                self._cycle_history.append({
                    "cycle": cycle,
                    "proposal_id": proposal.proposal_id,
                    "decision": decision,
                    "metrics": metrics,
                })

        return results

    # -- Proposal generation ---------------------------------------------------

    def propose(self) -> List[HarnessProposal]:
        """Generate improvement proposals based on recent failures.

        Returns
        -------
        list[HarnessProposal]
        """
        recent_traces = self._get_recent_failures()
        if not recent_traces:
            return []

        self.clusterer.cluster(recent_traces)
        signatures = self.clusterer.get_all_signatures()

        proposals: List[HarnessProposal] = []

        # Propose verifier additions for high-frequency failure clusters
        for sig in signatures:
            if sig.frequency >= 2:
                proposals.append(self._propose_verifier_for_cluster(sig))

        # Propose surface additions for uncovered failure domains
        uncovered = self._find_uncovered_domains(signatures)
        for domain in uncovered:
            proposals.append(self._propose_surface_for_domain(domain))

        # Always include at least one no-op diagnostic proposal
        if not proposals:
            proposals.append(
                HarnessProposal(
                    proposal_id=f"diag-{uuid.uuid4().hex[:8]}",
                    parent_version=getattr(self.config, "version", "dev"),
                    rationale="Diagnostic cycle — no failures to address",
                    changes={},
                    estimated_impact={"type": "diagnostic"},
                )
            )

        return proposals

    # -- Evaluation ------------------------------------------------------------

    def _evaluate(
        self,
        proposal: HarnessProposal,
        held_in_scenarios: Optional[List[Any]],
    ) -> Dict[str, Any]:
        """Evaluate a proposal against held-in scenarios.

        Returns a metrics dict with at least ``held_in_score``,
        ``total_cost_usd``, and ``surface_scores``.
        """
        metrics: Dict[str, Any] = {
            "held_in_score": 0.0,
            "total_cost_usd": 0.0,
            "surface_scores": {},
            "gates_passed": "-",
            "risk_assessment": "unknown",
        }

        if not held_in_scenarios:
            # No scenarios — use trace-based heuristic
            recent = self._get_recent_failures()
            if recent:
                pass_rate = sum(
                    1 for t in recent if t.verdict == Verdict.PASS
                ) / len(recent)
                metrics["held_in_score"] = pass_rate
            return metrics

        variant_config = self.config
        if proposal.changes:
            try:
                normalised = self._normalise_changes(proposal.changes)
                if normalised:
                    variant_config = self.config.apply_changes(normalised)
            except Exception as exc:
                logger.warning("Failed to apply proposal changes: %s", exc)

        split = ScenarioSplit(held_in_scenarios, held_out_ratio=0.0)
        runner = SingleRunner(version_tag=getattr(variant_config, "version", "dev"))

        start = time.perf_counter()
        raw = runner.run(variant_config, split.held_in, self.registry)
        duration_ms = (time.perf_counter() - start) * 1000

        traces = raw.get("traces", [])
        for tr in traces:
            self.trace_store.record(tr)

        metrics["held_in_score"] = raw.get("aggregate_score", 0.0)
        metrics["total_cost_usd"] = raw.get("cost_usd", 0.0)
        metrics["duration_ms"] = duration_ms

        # Per-surface scores
        surface_scores: Dict[str, List[float]] = {}
        for tr in traces:
            for surf in (tr.metadata or {}).get("surfaces", []):
                surface_scores.setdefault(surf, []).append(
                    1.0 if tr.verdict == Verdict.PASS else 0.0
                )
        metrics["surface_scores"] = {
            s: sum(scores) / len(scores) if scores else 0.0
            for s, scores in surface_scores.items()
        }

        return metrics

    # -- Decision ---------------------------------------------------------------

    def decide(
        self,
        proposal: HarnessProposal,
        metrics: Dict[str, Any],
    ) -> str:
        """Decide whether to accept, reject, or defer a proposal.

        Returns one of ``"accept"``, ``"reject"``, or ``"review"``.
        """
        score = metrics.get("held_in_score", metrics.get("score", 0.0))

        if score >= self.auto_accept_threshold:
            return "accept"
        if score <= self.auto_reject_threshold:
            return "reject"
        return "review"

    # -- Accept ------------------------------------------------------------------

    def _accept(self, proposal: HarnessProposal, metrics: Dict[str, Any]) -> None:
        """Commit an accepted proposal to the lineage."""
        try:
            normalised = self._normalise_changes(proposal.changes)
            if normalised:
                self.config = self.config.apply_changes(normalised)
            self.lineage.commit(self.config, proposal_id=proposal.proposal_id)
            logger.info(
                "Accepted proposal %s (score=%.4f)",
                proposal.proposal_id,
                metrics.get("held_in_score", 0.0),
            )
        except Exception as exc:
            logger.error("Failed to commit proposal %s: %s", proposal.proposal_id, exc)

    # -- Helpers ------------------------------------------------------------------

    def _get_recent_failures(self, limit: int = 100) -> List[TraceRecord]:
        """Retrieve recent non-pass traces from the store."""
        all_traces = self.trace_store.query()
        failures = [
            t for t in all_traces
            if t.verdict not in (Verdict.PASS, Verdict.SKIP)
        ]
        return failures[:limit]

    def _find_uncovered_domains(
        self, signatures: List[FailureSignature]
    ) -> List[str]:
        """Identify failure domains not covered by current surfaces."""
        covered = {s.name for s in getattr(self.config, "surfaces", [])}
        domains: List[str] = []
        for sig in signatures:
            for surf in sig.surfaces:
                if surf not in covered and surf not in domains:
                    domains.append(surf)
        return domains

    def _propose_verifier_for_cluster(self, sig: FailureSignature) -> HarnessProposal:
        """Propose adding a verifier targeting a failure cluster."""
        return HarnessProposal(
            proposal_id=f"verifier-{uuid.uuid4().hex[:8]}",
            parent_version=getattr(self.config, "version", "dev"),
            rationale=(
                f"Add verifier for failure cluster {sig.cluster_id} "
                f"(frequency={sig.frequency})"
            ),
            changes={
                "_scopes": [
                    {
                        "scope": "add_verifier",
                        "name": f"cluster_{sig.cluster_id[:20]}",
                        "verifier_type": sig.verifier_type or "fuzzy",
                    }
                ]
            },
            estimated_impact={
                "cluster_id": sig.cluster_id,
                "frequency": sig.frequency,
            },
        )

    def _propose_surface_for_domain(self, domain: str) -> HarnessProposal:
        """Propose adding a surface for an uncovered failure domain."""
        return HarnessProposal(
            proposal_id=f"surface-{uuid.uuid4().hex[:8]}",
            parent_version=getattr(self.config, "version", "dev"),
            rationale=f"Add surface for uncovered domain: {domain}",
            changes={
                "_scopes": [
                    {
                        "scope": "add_surface",
                        "name": domain,
                        "config": {"type": "API", "risk_score": 0.5},
                    }
                ]
            },
            estimated_impact={"domain": domain},
        )

    @staticmethod
    def _normalise_changes(changes: Dict[str, Any]) -> Dict[str, Any]:
        """Convert proposal changes to the format expected by apply_changes."""
        if not changes:
            return {}
        normalised: Dict[str, Any] = {}
        if "add_surfaces" in changes:
            normalised["add_surfaces"] = changes["add_surfaces"]
        if "remove_surfaces" in changes:
            normalised["remove_surfaces"] = changes["remove_surfaces"]
        if "tune_verifier" in changes:
            normalised["tune_verifier"] = changes["tune_verifier"]
        if "set_thresholds" in changes:
            normalised["set_thresholds"] = changes["set_thresholds"]
        return normalised

    def get_cycle_history(self) -> List[Dict[str, Any]]:
        """Return the history of all completed cycles."""
        return list(self._cycle_history)
