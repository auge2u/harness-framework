"""Escalation policy: when System 1 hands off to System 2.

An :class:`EscalationPolicy` decides whether a
:class:`~harness.reflex.primitives.ReflexResult` should be escalated to a
slower, more expensive System 2 reasoner, and it accumulates statistics
about those decisions for observability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:  # pragma: no cover - typing only
    from harness.reflex.primitives import ReflexResult

__all__ = ["EscalationEvent", "EscalationPolicy"]

#: Recognised escalation reasons.
REASON_LOW_CONFIDENCE = "low_confidence"
REASON_THRESHOLD_EXCEEDED = "threshold_exceeded"
REASON_NOVEL_PATTERN = "novel_pattern"


@dataclass
class EscalationEvent:
    """Record of a single System 1 → System 2 handoff.

    Attributes:
        rule: Name of the rule or primitive that triggered the handoff.
        reason: One of ``"low_confidence"``, ``"threshold_exceeded"`` or
            ``"novel_pattern"``.
        value: The observed value that triggered escalation.
        threshold: The threshold the value was compared against.
        timestamp: When the escalation was recorded (UTC).
    """

    rule: str
    reason: str
    value: float
    threshold: float
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        """Normalise numeric fields to floats."""
        self.value = float(self.value)
        self.threshold = float(self.threshold)


class EscalationPolicy:
    """Defines when System 1 hands off to System 2. Tracks stats.

    Args:
        default_confidence_floor: Results whose confidence falls below
            this floor escalate with reason ``"low_confidence"``.
        default_score_ceiling: Score-primitive results at or above this
            ceiling escalate with reason ``"threshold_exceeded"``.
    """

    def __init__(
        self,
        default_confidence_floor: float = 0.5,
        default_score_ceiling: float = 7.0,
    ) -> None:
        self.default_confidence_floor = float(default_confidence_floor)
        self.default_score_ceiling = float(default_score_ceiling)
        self._total_checks = 0
        self._events: list[EscalationEvent] = []
        self._by_reason: Dict[str, int] = {
            REASON_LOW_CONFIDENCE: 0,
            REASON_THRESHOLD_EXCEEDED: 0,
            REASON_NOVEL_PATTERN: 0,
        }

    def should_escalate(self, result: "ReflexResult", rule: str = "") -> bool:
        """Decide whether *result* should be handed to System 2.

        Every call counts toward :attr:`total_checks`; when escalation is
        warranted an :class:`EscalationEvent` is recorded automatically.

        Args:
            result: The reflex result to evaluate.
            rule: Optional rule name used in the recorded event.

        Returns:
            ``True`` when the result should escalate.
        """
        self._total_checks += 1
        escalate = bool(result.escalate)
        reason = ""
        value = 0.0
        threshold = self.default_confidence_floor

        if result.primitive == "score":
            value = float(result.value)
            threshold = self.default_score_ceiling
            if value >= self.default_score_ceiling:
                escalate = True
                reason = REASON_THRESHOLD_EXCEEDED
        elif result.primitive == "bool":
            value = float(result.value)
            threshold = float(result.detail.get("block_threshold", 0.85))
            if escalate:
                reason = REASON_THRESHOLD_EXCEEDED

        if not reason and result.confidence < self.default_confidence_floor:
            escalate = True
            reason = REASON_LOW_CONFIDENCE
            value = float(result.confidence)
            threshold = self.default_confidence_floor

        if escalate and not reason:
            reason = REASON_NOVEL_PATTERN
            try:
                value = float(result.value)
            except (TypeError, ValueError):
                value = float(result.confidence)

        if escalate:
            self.record(
                EscalationEvent(
                    rule=rule or result.primitive,
                    reason=reason,
                    value=value,
                    threshold=threshold,
                )
            )
        return escalate

    def record(self, event: EscalationEvent) -> None:
        """Record an :class:`EscalationEvent` and update reason counters."""
        self._events.append(event)
        self._by_reason[event.reason] = self._by_reason.get(event.reason, 0) + 1

    def stats(self) -> Dict[str, Any]:
        """Return accumulated escalation statistics.

        Returns:
            Dict with keys ``total_checks``, ``escalations``,
            ``escalation_rate`` and ``by_reason`` (per-reason counts).
        """
        escalations = len(self._events)
        rate = escalations / self._total_checks if self._total_checks else 0.0
        return {
            "total_checks": self._total_checks,
            "escalations": escalations,
            "escalation_rate": rate,
            "by_reason": dict(self._by_reason),
        }

    def reset(self) -> None:
        """Clear all accumulated statistics and recorded events."""
        self._total_checks = 0
        self._events.clear()
        self._by_reason = {
            REASON_LOW_CONFIDENCE: 0,
            REASON_THRESHOLD_EXCEEDED: 0,
            REASON_NOVEL_PATTERN: 0,
        }
