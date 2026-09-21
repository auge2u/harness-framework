"""Reflex primitives: bool / score / choice with escalation baked in.

:class:`ReflexPrimitives` is a thin convenience wrapper over a
:class:`~harness.reflex.backend.ReflexBackend`.  It converts raw backend
probabilities into :class:`ReflexResult` values, applies escalation
thresholds, consults an optional
:class:`~harness.reflex.escalation.EscalationPolicy`, and can record
traces to an optional duck-typed trace store.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from harness.reflex.backend import ReflexBackend

if TYPE_CHECKING:  # pragma: no cover - typing only
    from harness.reflex.escalation import EscalationPolicy
    from harness.reflex.rubrics import Rubric

__all__ = ["ReflexResult", "ReflexPrimitives"]


@dataclass
class ReflexResult:
    """Result of any reflex check.

    Attributes:
        primitive: Which primitive produced the result — ``"bool"``,
            ``"score"`` or ``"choice"``.
        value: Probability (bool), score (score) or chosen option name
            (choice).
        confidence: Confidence in the result, in ``[0.0, 1.0]``.
        latency_ms: Simulated or measured System 1 latency.
        escalate: ``True`` when the result should be handed to System 2.
        detail: Primitive-specific structured detail.
    """

    primitive: str
    value: Any
    confidence: float
    latency_ms: float
    escalate: bool = False
    detail: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalise mutable defaults and numeric fields."""
        if self.detail is None:
            self.detail = {}
        self.confidence = float(self.confidence)
        self.latency_ms = float(self.latency_ms)
        self.escalate = bool(self.escalate)


class ReflexPrimitives:
    """Thin convenience wrapper over a ReflexBackend with escalation logic.

    Args:
        backend: The :class:`~harness.reflex.backend.ReflexBackend` used to
            evaluate the primitives.
        escalation: Optional
            :class:`~harness.reflex.escalation.EscalationPolicy` consulted
            after every primitive call.
        trace_store: Optional duck-typed store (any object with a
            ``record(trace)`` method, e.g.
            :class:`~harness.store.trace_store.TraceStore`) that receives a
            trace record for every reflex decision.
    """

    def __init__(
        self,
        backend: ReflexBackend,
        escalation: Optional["EscalationPolicy"] = None,
        trace_store: Optional[Any] = None,
    ) -> None:
        self.backend = backend
        self.escalation = escalation
        self.trace_store = trace_store

    # -- Primitives ----------------------------------------------------------

    def bool_gate(
        self,
        question: str,
        context: str,
        block_threshold: float = 0.85,
    ) -> ReflexResult:
        """Evaluate a yes/no question against *context*.

        Args:
            question: Natural-language yes/no question.
            context: Text the question is evaluated against.
            block_threshold: Probability at or above which the result
                escalates (blocks).

        Returns:
            A :class:`ReflexResult` with ``primitive="bool"`` and
            ``value`` equal to the probability.
        """
        probability = self.backend.bool_check(question, context)
        escalate = probability >= block_threshold
        confidence = probability if probability >= 0.5 else 1.0 - probability
        result = ReflexResult(
            primitive="bool",
            value=probability,
            confidence=round(confidence, 4),
            latency_ms=getattr(self.backend, "last_latency_ms", 0.0),
            escalate=escalate,
            detail={
                "question": question,
                "block_threshold": float(block_threshold),
                "action": "BLOCK" if escalate else "PASS",
            },
        )
        return self._finalize(result, rule="bool_gate")

    def score_gate(self, input_text: str, rubric: "Rubric") -> ReflexResult:
        """Score *input_text* against *rubric*.

        Escalates when the score is at or above
        ``rubric.escalation_threshold``.

        Args:
            input_text: Text to evaluate.
            rubric: The :class:`~harness.reflex.rubrics.Rubric` to apply.

        Returns:
            A :class:`ReflexResult` with ``primitive="score"`` and
            ``value`` equal to the rubric score (0.0 – 10.0).
        """
        score = self.backend.score_check(input_text, rubric)
        escalate = score >= rubric.escalation_threshold
        # Confidence is highest near the extremes of the spectrum.
        confidence = round(0.5 + abs(score - 5.0) / 10.0, 4)
        result = ReflexResult(
            primitive="score",
            value=score,
            confidence=confidence,
            latency_ms=getattr(self.backend, "last_latency_ms", 0.0),
            escalate=escalate,
            detail={
                "rubric_id": rubric.rubric_id,
                "rubric_name": rubric.name,
                "rubric_version": rubric.version,
                "escalation_threshold": rubric.escalation_threshold,
                "pass_threshold": rubric.pass_threshold,
                "passes": score < rubric.pass_threshold,
            },
        )
        return self._finalize(result, rule=f"score_gate:{rubric.name}")

    def route(
        self,
        input_text: str,
        options: List[Dict[str, str]],
        confidence_floor: float = 0.5,
    ) -> ReflexResult:
        """Pick the best-matching option for *input_text*.

        Escalates when the top option's probability falls below
        *confidence_floor*.

        Args:
            input_text: Text to classify.
            options: List of ``{"name": ..., "description": ...}`` dicts.
            confidence_floor: Minimum top probability before escalation.

        Returns:
            A :class:`ReflexResult` with ``primitive="choice"`` and
            ``value`` equal to the chosen option name (``"none"`` when
            *options* is empty).
        """
        probabilities = self.backend.choice_check(input_text, options)
        if not probabilities:
            result = ReflexResult(
                primitive="choice",
                value="none",
                confidence=0.0,
                latency_ms=getattr(self.backend, "last_latency_ms", 0.0),
                escalate=True,
                detail={
                    "probabilities": {},
                    "confidence_floor": float(confidence_floor),
                    "reason": "no_options",
                },
            )
            return self._finalize(result, rule="route")

        # max() keeps insertion order on ties -> deterministic tie-break.
        top_name = max(probabilities, key=lambda name: probabilities[name])
        top_probability = probabilities[top_name]
        escalate = top_probability < confidence_floor
        result = ReflexResult(
            primitive="choice",
            value=top_name,
            confidence=round(top_probability, 4),
            latency_ms=getattr(self.backend, "last_latency_ms", 0.0),
            escalate=escalate,
            detail={
                "probabilities": dict(probabilities),
                "confidence_floor": float(confidence_floor),
            },
        )
        return self._finalize(result, rule="route")

    # -- Internals -----------------------------------------------------------

    def _finalize(self, result: ReflexResult, rule: str) -> ReflexResult:
        """Consult the escalation policy and record a trace."""
        if self.escalation is not None:
            policy_escalate = self.escalation.should_escalate(result, rule=rule)
            result.escalate = result.escalate or policy_escalate
        self._record_trace(result)
        return result

    def _record_trace(self, result: ReflexResult) -> None:
        """Record a trace for *result* when a duck-typed store is attached.

        The store is only required to expose ``record(trace)``; any
        recording failure is swallowed so tracing never breaks the reflex
        hot path.
        """
        if self.trace_store is None:
            return
        record = getattr(self.trace_store, "record", None)
        if not callable(record):
            return
        try:
            from harness.core.types import TraceRecord, Verdict

            value = result.value if isinstance(result.value, (int, float)) else str(result.value)
            trace = TraceRecord(
                trace_id=f"reflex-{uuid.uuid4().hex[:16]}",
                scenario_id=f"reflex.{result.primitive}",
                harness_version="reflex/1.0.0",
                timestamp=datetime.now(timezone.utc),
                inputs={"detail": dict(result.detail)},
                outputs={"value": value, "confidence": result.confidence},
                verdict=Verdict.FAIL if result.escalate else Verdict.PASS,
                latency_ms=result.latency_ms,
                metadata={"primitive": result.primitive, "escalate": result.escalate},
            )
            record(trace)
        except Exception:
            # Tracing is best-effort and must never break the reflex path.
            pass
