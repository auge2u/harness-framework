"""CircuitRunner — parallel variant execution with cost budgeting.

A *circuit* is a full harness run (held-in + held-out scenarios) for a
specific configuration variant.  The ``CircuitRunner`` manages multiple
variants, executes them with bounded parallelism, tracks cumulative cost,
and compares results.
"""

from __future__ import annotations

import copy
import math
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from harness.core.types import (
    FailureSignature,
    HarnessProposal,
    TraceRecord,
    Verdict,
)
from harness.core.config import HarnessConfig
from harness.core.registry import PluginRegistry
from harness.core.exceptions import BudgetExceededError
from harness.scenarios.split import ScenarioSplit
from harness.store.trace_store import TraceStore
from harness.analysis.clusterer import FailureClusterer
from harness.runners.runner import SingleRunner


# ---------------------------------------------------------------------------
# CircuitResult
# ---------------------------------------------------------------------------

@dataclass
class CircuitResult:
    """Result of running a single configuration *circuit*.

    Attributes
    ----------
    variant_id:
        Unique identifier for this circuit run.
    config_version:
        The version string of the configuration that was evaluated.
    proposal:
        The :class:`HarnessProposal` that produced this variant, or ``None``
        if it was the base configuration.
    held_in_results:
        Raw return dict from :class:`SingleRunner` for the held-in split.
    held_out_results:
        Raw return dict from :class:`SingleRunner` for the held-out split,
        or an empty dict if held-out was not run.
    aggregate_score:
        Overall score (weighted: 60% held-in + 40% held-out).
    cost_usd:
        Total cost in USD.
    duration_ms:
        Wall-clock time in milliseconds.
    failure_clusters:
        Failure clusters extracted from held-in traces.
    traces:
        All :class:`TraceRecord` objects produced during the circuit.
    """

    variant_id: str
    config_version: str
    proposal: Optional[HarnessProposal] = None
    held_in_results: Dict[str, Any] = field(default_factory=dict)
    held_out_results: Dict[str, Any] = field(default_factory=dict)
    aggregate_score: float = 0.0
    cost_usd: float = 0.0
    duration_ms: float = 0.0
    failure_clusters: List[FailureSignature] = field(default_factory=list)
    traces: List[TraceRecord] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CircuitRunner
# ---------------------------------------------------------------------------

class CircuitRunner:
    """Execute multiple configuration variants in parallel with cost control.

    Parameters
    ----------
    registry:
        Plugin registry for looking up verifiers.
    trace_store:
        Persistent store for trace records.
    max_parallel:
        Maximum number of variants to execute concurrently.
    cost_budget_usd:
        Cumulative cost cap in USD.
    """

    def __init__(
        self,
        registry: PluginRegistry,
        trace_store: TraceStore,
        max_parallel: int = 4,
        cost_budget_usd: float = 10.0,
    ) -> None:
        self.registry = registry
        self.trace_store = trace_store
        self.max_parallel = max(max_parallel, 1)
        self.cost_budget_usd = cost_budget_usd
        self._cumulative_cost = 0.0
        self._failure_clusterer = FailureClusterer()

    # -- Single circuit -------------------------------------------------------

    def run_single(
        self,
        config: HarnessConfig,
        scenarios: ScenarioSplit,
    ) -> CircuitResult:
        """Run one circuit: held-in followed by held-out scenarios.

        Traces are persisted to :attr:`trace_store` after each split.
        """
        variant_id = f"circuit-{uuid.uuid4().hex[:12]}"
        start = time.perf_counter()
        all_traces: List[TraceRecord] = []

        runner = SingleRunner(version_tag=getattr(config, "version", "dev"))

        # ---- Held-in --------------------------------------------------------
        held_in_raw = runner.run(config, scenarios.held_in, self.registry)
        held_in_traces = held_in_raw.get("traces", [])
        all_traces.extend(held_in_traces)
        for tr in held_in_traces:
            self.trace_store.record(tr)

        # Cluster failures from held-in
        self._failure_clusterer.cluster(held_in_traces)
        failure_clusters = [
            self._failure_clusterer.get_signature(cid)
            for cid in self._failure_clusterer._clusters.keys()
            if len(self._failure_clusterer._clusters[cid])
            >= self._failure_clusterer.min_cluster_size
        ]

        # ---- Held-out -------------------------------------------------------
        held_out_raw: Dict[str, Any] = {}
        if scenarios.held_out:
            held_out_raw = runner.run(config, scenarios.held_out, self.registry)
            held_out_traces = held_out_raw.get("traces", [])
            all_traces.extend(held_out_traces)
            for tr in held_out_traces:
                self.trace_store.record(tr)

        # ---- Aggregate ------------------------------------------------------
        hi_score = held_in_raw.get("aggregate_score", 0.0)
        hi_cost = held_in_raw.get("cost_usd", 0.0)
        ho_score = held_out_raw.get("aggregate_score", 0.0)
        ho_cost = held_out_raw.get("cost_usd", 0.0)

        total_cost = hi_cost + ho_cost
        if scenarios.held_out and ho_score > 0:
            aggregate = 0.6 * hi_score + 0.4 * ho_score
        else:
            aggregate = hi_score

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        self._cumulative_cost += total_cost

        return CircuitResult(
            variant_id=variant_id,
            config_version=getattr(config, "version", "dev"),
            held_in_results=held_in_raw,
            held_out_results=held_out_raw,
            aggregate_score=aggregate,
            cost_usd=total_cost,
            duration_ms=elapsed_ms,
            failure_clusters=failure_clusters,
            traces=all_traces,
        )

    # -- Multiple variants (parallel) -----------------------------------------

    def run_variants(
        self,
        base_config: HarnessConfig,
        proposals: List[HarnessProposal],
        scenarios: ScenarioSplit,
        run_held_out_on_best: bool = True,
        held_out_threshold: float = 0.8,
    ) -> List[CircuitResult]:
        """Evaluate *proposals* by applying each to *base_config* and running
        the held-in scenarios in parallel.

        Cost budget is checked after each variant completes.

        Parameters
        ----------
        base_config:
            The baseline configuration.
        proposals:
            Proposed changes to evaluate.
        scenarios:
            Scenario split (only held-in used for the parallel phase).
        run_held_out_on_best:
            If ``True``, run held-out on top-performing variant(s).
        held_out_threshold:
            Minimum held-in score to qualify for held-out phase.

        Returns
        -------
        list[CircuitResult]
            One result per proposal, ordered by proposal order.

        Raises
        ------
        BudgetExceededError
            When the cumulative cost exceeds the budget.
        """
        # Build variant configs
        variant_specs: List[Tuple[HarnessProposal, HarnessConfig]] = []
        for proposal in proposals:
            variant_cfg = self._apply_proposal(base_config, proposal)
            variant_specs.append((proposal, variant_cfg))

        results: List[Tuple[int, CircuitResult]] = []

        # ---- Parallel held-in runs ------------------------------------------
        with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
            futures: Dict[Any, Tuple[HarnessProposal, int]] = {}
            for idx, (proposal, variant_cfg) in enumerate(variant_specs):
                future = pool.submit(
                    self._run_held_in_only, variant_cfg, scenarios, proposal
                )
                futures[future] = (proposal, idx)

            for future in as_completed(futures):
                proposal, original_idx = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = CircuitResult(
                        variant_id=f"circuit-failed-{uuid.uuid4().hex[:8]}",
                        config_version=getattr(base_config, "version", "dev"),
                        proposal=proposal,
                        held_in_results={"error": str(exc)},
                        aggregate_score=0.0,
                        cost_usd=0.0,
                        duration_ms=0.0,
                    )

                results.append((original_idx, result))
                self._cumulative_cost += result.cost_usd

                if self._cumulative_cost > self.cost_budget_usd:
                    results.sort(key=lambda x: x[0])
                    raise BudgetExceededError(
                        f"Cost budget exceeded: ${self._cumulative_cost:.4f} "
                        f"> ${self.cost_budget_usd:.4f}",
                    )

        # Sort back to proposal order
        results.sort(key=lambda x: x[0])
        ordered_results = [r for _, r in results]

        # ---- Optional held-out for best variant ----------------------------
        if run_held_out_on_best and scenarios.held_out:
            best_idx = self._pick_best_for_held_out(ordered_results, held_out_threshold)
            if best_idx is not None:
                best_result = ordered_results[best_idx]
                best_proposal = best_result.proposal
                best_config = (
                    self._apply_proposal(base_config, best_proposal)
                    if best_proposal else base_config
                )

                runner = SingleRunner(version_tag=getattr(best_config, "version", "dev"))
                held_out_raw = runner.run(
                    best_config, scenarios.held_out, self.registry
                )
                ho_traces = held_out_raw.get("traces", [])
                for tr in ho_traces:
                    self.trace_store.record(tr)

                hi_score = best_result.held_in_results.get("aggregate_score", 0.0)
                ho_score = held_out_raw.get("aggregate_score", 0.0)
                ho_cost = held_out_raw.get("cost_usd", 0.0)

                best_result.held_out_results = held_out_raw
                best_result.aggregate_score = 0.6 * hi_score + 0.4 * ho_score
                best_result.cost_usd += ho_cost
                best_result.traces.extend(ho_traces)
                self._cumulative_cost += ho_cost

        return ordered_results

    # -- Comparison & selection -----------------------------------------------

    def compare(self, results: List[CircuitResult]) -> Dict[str, Any]:
        """Rank circuit results and compute statistical comparisons.

        Returns a dict with ``ranking``, ``score_diffs``, ``best_variant_id``,
        and ``p_values`` (normal-approximation).
        """
        if not results:
            return {
                "ranking": [],
                "score_diffs": {},
                "best_variant_id": None,
                "p_values": {},
            }

        ranking = sorted(
            [(r.variant_id, r.aggregate_score) for r in results],
            key=lambda x: x[1],
            reverse=True,
        )
        best_id = ranking[0][0]
        best_score = ranking[0][1]

        score_diffs = {
            r.variant_id: r.aggregate_score - best_score
            for r in results
        }

        p_values: Dict[str, float] = {}
        best_result = next(r for r in results if r.variant_id == best_id)
        best_scores = self._extract_per_scenario_scores(best_result)

        for r in results:
            if r.variant_id == best_id:
                p_values[r.variant_id] = 1.0
                continue
            variant_scores = self._extract_per_scenario_scores(r)
            p_values[r.variant_id] = self._approximate_p_value(
                best_scores, variant_scores
            )

        return {
            "ranking": ranking,
            "score_diffs": score_diffs,
            "best_variant_id": best_id,
            "p_values": p_values,
        }

    def select_best(self, results: List[CircuitResult]) -> CircuitResult:
        """Return the :class:`CircuitResult` with the highest
        ``aggregate_score``.

        Raises
        ------
        ValueError
            If *results* is empty.
        """
        if not results:
            raise ValueError("No circuit results to select from")
        return max(results, key=lambda r: r.aggregate_score)

    # -- Internal helpers -----------------------------------------------------

    def _run_held_in_only(
        self,
        config: HarnessConfig,
        scenarios: ScenarioSplit,
        proposal: Optional[HarnessProposal],
    ) -> CircuitResult:
        """Run held-in scenarios only (for parallel execution)."""
        variant_id = f"circuit-{uuid.uuid4().hex[:12]}"
        start = time.perf_counter()

        runner = SingleRunner(version_tag=getattr(config, "version", "dev"))
        held_in_raw = runner.run(config, scenarios.held_in, self.registry)

        held_in_traces = held_in_raw.get("traces", [])
        for tr in held_in_traces:
            self.trace_store.record(tr)

        # Cluster failures
        self._failure_clusterer.cluster(held_in_traces)
        failure_clusters = [
            self._failure_clusterer.get_signature(cid)
            for cid in self._failure_clusterer._clusters.keys()
            if len(self._failure_clusterer._clusters[cid])
            >= self._failure_clusterer.min_cluster_size
        ]

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        cost = held_in_raw.get("cost_usd", 0.0)
        self._cumulative_cost += cost

        return CircuitResult(
            variant_id=variant_id,
            config_version=getattr(config, "version", "dev"),
            proposal=proposal,
            held_in_results=held_in_raw,
            aggregate_score=held_in_raw.get("aggregate_score", 0.0),
            cost_usd=cost,
            duration_ms=elapsed_ms,
            failure_clusters=failure_clusters,
            traces=held_in_traces,
        )

    @staticmethod
    def _apply_proposal(
        base: HarnessConfig, proposal: HarnessProposal
    ) -> HarnessConfig:
        """Apply *proposal* to *base* and return a new configuration.

        The proposal's ``changes`` dict is converted into the format
        expected by :meth:`HarnessConfig.apply_changes`.
        """
        changes = proposal.changes if proposal.changes else {}

        # Normalise changes into the format apply_changes expects
        normalised: Dict[str, Any] = {}

        if "add_surfaces" in changes:
            normalised["add_surfaces"] = changes["add_surfaces"]
        if "remove_surfaces" in changes:
            normalised["remove_surfaces"] = changes["remove_surfaces"]
        if "tune_verifier" in changes:
            normalised["tune_verifier"] = changes["tune_verifier"]
        if "set_thresholds" in changes:
            normalised["set_thresholds"] = changes["set_thresholds"]

        # Handle legacy change formats (scope-based dicts)
        scope_changes = changes.get("_scopes", [])
        if scope_changes:
            add_surfaces = []
            remove_surfaces = []
            for change in scope_changes:
                scope = change.get("scope", "")
                if scope == "add_surface":
                    add_surfaces.append({
                        "name": change["name"],
                        **change.get("config", {}),
                    })
                elif scope == "remove_surface":
                    remove_surfaces.append(change["name"])
                elif scope == "tune_threshold":
                    # Find the surface and update its schema
                    for surf in base.surfaces:
                        if surf.name == change["name"]:
                            surf.schema["threshold"] = change["value"]
                            break
                elif scope == "add_verifier":
                    vtype = change.get("verifier_type", change.get("name", ""))
                    if vtype and vtype not in [v.get("type", "") for v in base.verifiers]:
                        base.verifiers.append({"type": vtype})
                elif scope == "remove_verifier":
                    base.verifiers = [
                        v for v in base.verifiers
                        if v.get("type", "") != change.get("name", "")
                    ]
                elif scope == "tune_held_out":
                    base.held_out_ratio = change["value"]
                elif scope == "add_scenario":
                    if change["name"] not in base.scenarios:
                        base.scenarios[change["name"]] = {}
                elif scope == "remove_scenario":
                    base.scenarios.pop(change["name"], None)

            if add_surfaces:
                normalised["add_surfaces"] = add_surfaces
            if remove_surfaces:
                normalised["remove_surfaces"] = remove_surfaces

        if normalised:
            return base.apply_changes(normalised)
        return copy.deepcopy(base)

    @staticmethod
    def _extract_per_scenario_scores(result: CircuitResult) -> List[float]:
        """Extract per-scenario scores from a circuit result."""
        hi = result.held_in_results
        if not hi or "scenario_results" not in hi:
            return [result.aggregate_score]
        scores = []
        for vr in hi["scenario_results"].values():
            if hasattr(vr, "score"):
                scores.append(vr.score)
            elif isinstance(vr, dict):
                scores.append(vr.get("score", 0.0))
            else:
                scores.append(0.0)
        return scores if scores else [result.aggregate_score]

    @staticmethod
    def _approximate_p_value(
        scores_a: List[float], scores_b: List[float]
    ) -> float:
        """Approximate a two-tailed p-value using normal difference-of-means."""
        n_a = len(scores_a)
        n_b = len(scores_b)
        if n_a == 0 or n_b == 0:
            return 1.0

        mean_a = sum(scores_a) / n_a
        mean_b = sum(scores_b) / n_b

        var_a = sum((x - mean_a) ** 2 for x in scores_a) / max(n_a, 1)
        var_b = sum((x - mean_b) ** 2 for x in scores_b) / max(n_b, 1)

        se = math.sqrt(var_a / max(n_a, 1) + var_b / max(n_b, 1))
        if se == 0:
            return 1.0 if abs(mean_a - mean_b) < 1e-9 else 0.0

        z = abs(mean_a - mean_b) / se
        return 2.0 * (1.0 - _normal_cdf(z))

    @staticmethod
    def _pick_best_for_held_out(
        results: List[CircuitResult], threshold: float
    ) -> Optional[int]:
        """Return the index of the best result that meets *threshold*."""
        best_idx: Optional[int] = None
        best_score = -1.0
        for idx, r in enumerate(results):
            hi_score = r.held_in_results.get("aggregate_score", 0.0)
            if hi_score >= threshold and hi_score > best_score:
                best_score = hi_score
                best_idx = idx
        return best_idx


# ---------------------------------------------------------------------------
# Normal CDF helper (avoids scipy dependency)
# ---------------------------------------------------------------------------

def _normal_cdf(x: float) -> float:
    """Approximate the standard normal CDF using Abramowitz & Stegun."""
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911

    sign = 1.0 if x >= 0 else -1.0
    x = abs(x) / math.sqrt(2.0)

    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)

    return 0.5 * (1.0 + sign * y)
