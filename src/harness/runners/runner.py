"""Runner implementations for executing scenarios."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from harness.core.types import TraceRecord, Verdict, VerificationResult
from harness.core.config import HarnessConfig
from harness.core.registry import PluginRegistry
from harness.scenarios.base import Scenario
from harness.verifiers.builtin import ExactVerifier


class SingleRunner:
    """Runner that executes scenarios sequentially and produces trace records."""

    def __init__(self, version_tag: str = ""):
        self.version_tag = version_tag
        self.traces: List[TraceRecord] = []

    def run(
        self,
        config: HarnessConfig,
        scenarios: List[Scenario],
        registry: PluginRegistry,
        agent: Any = None,
    ) -> Dict[str, Any]:
        """Run all scenarios and return aggregated results."""
        start = time.time()
        scenario_results: Dict[str, Dict[str, Any]] = {}
        total_cost = 0.0
        verdict_counts: Dict[str, int] = {}

        for scenario in scenarios:
            trace, vresult = self._run_single(scenario, config, registry, agent)
            self.traces.append(trace)

            scenario_results[scenario.scenario_id] = {
                "verdict": trace.verdict,
                "score": vresult.score if vresult else 0.0,
                "latency_ms": trace.latency_ms,
            }

            total_cost += trace.cost_usd
            vname = trace.verdict.name if trace.verdict else "SKIP"
            verdict_counts[vname] = verdict_counts.get(vname, 0) + 1

        duration_ms = (time.time() - start) * 1000
        scores = [
            r["score"] for r in scenario_results.values()
            if r["verdict"] == Verdict.PASS
        ]
        aggregate = sum(scores) / len(scores) if scores else 0.0

        return {
            "scenario_results": scenario_results,
            "aggregate_score": aggregate,
            "duration_ms": duration_ms,
            "cost_usd": total_cost,
            "verdict_counts": verdict_counts,
            "traces": self.traces,
        }

    def _run_single(
        self,
        scenario: Scenario,
        config: HarnessConfig,
        registry: PluginRegistry,
        agent: Any = None,
    ) -> tuple[TraceRecord, Optional[VerificationResult]]:
        """Run a single scenario and return a trace record."""
        ctx = scenario.setup()
        run_start = time.time()

        try:
            result_data = scenario.run(agent, ctx)
            latency_ms = (time.time() - run_start) * 1000
            scenario.teardown(ctx)

            expected = scenario.get_expected()
            vresult: Optional[VerificationResult] = None
            if expected:
                try:
                    verifier_cls = registry.get_verifier("exact")
                    verifier = verifier_cls()
                    vresult = verifier.verify(expected, result_data)
                    verdict = vresult.verdict
                except Exception:
                    verdict = Verdict.FAIL
                    vresult = VerificationResult(verdict=Verdict.FAIL, score=0.0)
            else:
                verdict = Verdict.PASS
                vresult = VerificationResult(verdict=Verdict.PASS, score=1.0)

        except Exception as e:
            latency_ms = (time.time() - run_start) * 1000
            result_data = {"error": str(e)}
            verdict = Verdict.FAIL
            vresult = VerificationResult(verdict=Verdict.FAIL, score=0.0)
            try:
                scenario.teardown(ctx)
            except Exception:
                pass

        trace = TraceRecord(
            trace_id=f"trace-{scenario.scenario_id}-{time.time()}",
            scenario_id=scenario.scenario_id,
            harness_version=config.version,
            timestamp=__import__("datetime").datetime.now(),
            inputs={},
            outputs=result_data,
            verdict=verdict,
            verifier_results={"exact": {"score": vresult.score if vresult else 0.0}},
            latency_ms=latency_ms,
            cost_usd=0.01,
            metadata={"surfaces": scenario.surfaces},
        )
        return trace, vresult
