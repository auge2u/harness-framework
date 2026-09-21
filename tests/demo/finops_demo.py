"""FinOps demo scenario — demonstrates telemetry cost attribution."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from harness.core.types import TraceRecord, Verdict
from harness.scenarios.base import Scenario
from harness.store.trace_store import TraceStore
from harness.telemetry.metrics import TelemetryCollector, TelemetryEvent
from harness.telemetry.finops import FinOpsMetrics


class FinOpsDemoScenario(Scenario):
    """Demo scenario that exercises the FinOps telemetry pipeline."""

    scenario_id = "finops_demo"
    name = "FinOps Demo"
    description = "Demonstrates cost attribution across surfaces"
    tags = ["demo", "finops"]
    surfaces = ["cost_api", "analytics_engine", "report_ui"]
    difficulty = 0.3

    def setup(self) -> Dict[str, Any]:
        return {"store": TraceStore(":memory:"), "events": []}

    def run(self, agent: Any, context: Dict[str, Any]) -> Dict[str, Any]:
        store: TraceStore = context["store"]
        collector = TelemetryCollector(store)
        finops = FinOpsMetrics(collector)

        surfaces = ["cost_api", "analytics_engine", "report_ui"]
        for i, surface in enumerate(surfaces):
            event = TelemetryEvent(
                event_type="scenario_step",
                timestamp=datetime.now(),
                harness_version="0.4.0",
                scenario_id=self.scenario_id,
                surface=surface,
                cost_usd=0.01 * (i + 1),
                latency_ms=50.0 * (i + 1),
            )
            collector.emit(event)

        cost_by_surface = collector.get_cost_by_surface()
        report = finops.export()

        return {
            "cost_by_surface": cost_by_surface,
            "total_cost": report["summary"]["total_cost"],
            "efficiency_score": report["summary"]["efficiency_score"],
            "events_recorded": len(collector.get_events()),
        }

    def teardown(self, context: Dict[str, Any]) -> None:
        store = context.get("store")
        if store is not None:
            store.close()

    def get_expected(self) -> Optional[Dict[str, Any]]:
        return None


if __name__ == "__main__":
    scenario = FinOpsDemoScenario()
    ctx = scenario.setup()
    result = scenario.run(None, ctx)
    scenario.teardown(ctx)
    print("FinOps Demo Results:")
    for key, value in result.items():
        print(f"  {key}: {value}")
