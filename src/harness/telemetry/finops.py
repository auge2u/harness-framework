"""FinOpsMetrics compatibility layer."""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from harness.telemetry.metrics import TelemetryCollector


class FinOpsMetrics:
    """FinOps metrics collection and analysis."""

    def __init__(self, collector: TelemetryCollector):
        self.collector = collector

    def export(self) -> Dict[str, Any]:
        """Export metrics in FinOps-standard format."""
        cost_by_surface = self.collector.get_cost_by_surface()
        cost_by_scenario = self.collector.get_cost_by_scenario()
        daily_attribution = self.get_daily_attribution()
        forecast = self.get_budget_forecast()
        efficiency = self.collector.get_efficiency_score()

        return {
            "schema_version": "1.0",
            "exported_at": datetime.now().isoformat(),
            "summary": {
                "total_cost": sum(cost_by_surface.values()),
                "total_surfaces": len(cost_by_surface),
                "total_scenarios": len(cost_by_scenario),
                "efficiency_score": round(efficiency, 4),
                "forecast_next_30d": round(forecast, 4),
            },
            "cost_by_surface": {k: round(v, 6) for k, v in cost_by_surface.items()},
            "cost_by_scenario": {k: round(v, 6) for k, v in cost_by_scenario.items()},
            "daily_attribution": {
                k: {sk: round(sv, 6) for sk, sv in v.items()}
                for k, v in daily_attribution.items()
            },
        }

    def get_daily_attribution(self) -> Dict[str, Dict[str, float]]:
        """Get day -> {surface: cost} attribution."""
        traces = self.collector.trace_store.query()
        daily: Dict[str, Dict[str, float]] = {}
        for trace in traces:
            day = trace.timestamp.strftime("%Y-%m-%d")
            if day not in daily:
                daily[day] = {}
            surfaces = trace.metadata.get("surfaces", [])
            if isinstance(surfaces, str):
                surfaces = [surfaces]
            if not surfaces:
                surface = trace.metadata.get("surface", "unknown")
                surfaces = [surface]
            cost_per_surface = trace.cost_usd / len(surfaces)
            for surf in surfaces:
                daily[day][surf] = daily[day].get(surf, 0.0) + cost_per_surface
        return daily

    def get_budget_forecast(self, days_ahead: int = 30) -> float:
        """Linear extrapolation from 7-day average to forecast future spend."""
        now = datetime.now()
        week_ago = (now - timedelta(days=7)).isoformat()
        traces = self.collector.trace_store.query(since=week_ago)
        if not traces:
            return 0.0
        daily_costs: Dict[str, float] = {}
        for t in traces:
            day = t.timestamp.strftime("%Y-%m-%d")
            daily_costs[day] = daily_costs.get(day, 0.0) + t.cost_usd
        if not daily_costs:
            return 0.0
        avg_daily = statistics.mean(daily_costs.values())
        return avg_daily * days_ahead

    def alert_if_over_budget(
        self,
        budget: float,
        threshold_ratio: float = 0.8,
    ) -> Optional[str]:
        """Return alert string if forecast exceeds threshold ratio of budget."""
        forecast = self.get_budget_forecast()
        threshold = budget * threshold_ratio
        if forecast > threshold:
            return (
                f"ALERT: Forecasted spend ${forecast:.2f} exceeds "
                f"{threshold_ratio * 100:.0f}% of budget (${threshold:.2f} / ${budget:.2f})"
            )
        return None

    def get_cost_variance(self) -> Dict[str, float]:
        """Calculate cost variance (stddev / mean) across surfaces."""
        cost_by_surface = self.collector.get_cost_by_surface()
        if len(cost_by_surface) < 2:
            return {"variance": 0.0, "stddev": 0.0, "mean": 0.0}
        costs = list(cost_by_surface.values())
        mean = statistics.mean(costs)
        stddev = statistics.stdev(costs) if len(costs) > 1 else 0.0
        return {
            "variance": (stddev / mean) if mean > 0 else 0.0,
            "stddev": stddev,
            "mean": mean,
        }

    def get_top_cost_surfaces(self, n: int = 5) -> List[tuple[str, float]]:
        """Get the top N surfaces by cost."""
        cost_by_surface = self.collector.get_cost_by_surface()
        sorted_surfaces = sorted(cost_by_surface.items(), key=lambda x: x[1], reverse=True)
        return sorted_surfaces[:n]
