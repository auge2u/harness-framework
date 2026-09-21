"""Telemetry collection and cost attribution."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from harness.core.types import TraceRecord, Verdict
from harness.store.trace_store import TraceStore


@dataclass
class TelemetryEvent:
    """A telemetry event for tracking harness execution."""
    event_type: str  # "scenario_start", "scenario_end", "proposal", "decision", etc.
    timestamp: datetime
    harness_version: str
    scenario_id: Optional[str] = None
    surface: Optional[str] = None
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


class TelemetryCollector:
    """Collects telemetry events and stores them as traces."""

    def __init__(self, trace_store: TraceStore):
        self.trace_store = trace_store
        self._events: List[TelemetryEvent] = []

    def emit(self, event: TelemetryEvent) -> None:
        """Emit a telemetry event, recording it as a trace."""
        self._events.append(event)
        trace = TraceRecord(
            trace_id=f"tel-{id(event)}-{event.timestamp.isoformat()}",
            scenario_id=f"__telemetry__{event.event_type}",
            harness_version=event.harness_version,
            timestamp=event.timestamp,
            inputs={"event_type": event.event_type, "scenario_id": event.scenario_id},
            outputs={},
            cost_usd=event.cost_usd,
            latency_ms=event.latency_ms,
            verdict=Verdict.PASS,
            metadata={"surface": event.surface, "event_type": event.event_type, **event.metadata},
        )
        self.trace_store.record(trace)

    def get_events(self) -> List[TelemetryEvent]:
        """Get all recorded events."""
        return list(self._events)

    def get_cost_by_surface(
        self,
        since: Optional[datetime] = None,
    ) -> Dict[str, float]:
        """Aggregate cost by surface from trace store."""
        since_str = since.isoformat() if since else None
        traces = self.trace_store.query(since=since_str)
        costs: Dict[str, float] = {}
        for trace in traces:
            surfaces = trace.metadata.get("surfaces", [])
            if isinstance(surfaces, str):
                surfaces = [surfaces]
            if not surfaces:
                surface = trace.metadata.get("surface")
                if surface:
                    surfaces = [surface]
            if not surfaces:
                surfaces = ["unknown"]

            cost_per_surface = trace.cost_usd / len(surfaces) if surfaces else trace.cost_usd
            for surf in surfaces:
                costs[surf] = costs.get(surf, 0.0) + cost_per_surface
        return costs

    def get_cost_by_scenario(
        self,
        since: Optional[datetime] = None,
    ) -> Dict[str, float]:
        """Aggregate cost by scenario from trace store."""
        since_str = since.isoformat() if since else None
        traces = self.trace_store.query(since=since_str)
        costs: Dict[str, float] = {}
        for trace in traces:
            sid = trace.scenario_id
            if sid.startswith("__telemetry__"):
                sid = trace.metadata.get("original_scenario_id", sid)
            costs[sid] = costs.get(sid, 0.0) + trace.cost_usd
        return costs

    def get_efficiency_score(self) -> float:
        """Calculate efficiency: passed_scenarios / total_cost, or 0 if no cost."""
        traces = self.trace_store.query()
        total_cost = sum(t.cost_usd for t in traces)
        if total_cost <= 0:
            return 0.0
        passed = sum(1 for t in traces if t.verdict == Verdict.PASS)
        return passed / total_cost

    def get_event_summary(self) -> Dict[str, int]:
        """Get a summary of event types."""
        summary: Dict[str, int] = {}
        for event in self._events:
            summary[event.event_type] = summary.get(event.event_type, 0) + 1
        return summary

    def reset(self) -> None:
        """Clear all recorded events."""
        self._events.clear()
