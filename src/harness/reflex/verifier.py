"""Reflexive verifier: plugs reflex scoring into the verifier framework."""

from __future__ import annotations

from typing import Any, Dict, Optional

from harness.core.types import Verdict, VerificationResult
from harness.reflex.primitives import ReflexPrimitives
from harness.reflex.rubrics import Rubric
from harness.verifiers.base import Verifier

__all__ = ["ReflexiveVerifier"]


class ReflexiveVerifier(Verifier):
    """Verifier that uses reflex scoring instead of exact/fuzzy matching.

    Holds a :class:`~harness.reflex.primitives.ReflexPrimitives` instance
    and a :class:`~harness.reflex.rubrics.Rubric`.  :meth:`verify` runs a
    score gate over the *actual* output and maps the score to a
    :class:`~harness.core.types.VerificationResult`: the raw 0–10 score is
    normalised to ``[0, 1]`` and the verdict is ``PASS`` when the score is
    strictly below ``rubric.pass_threshold``.

    Args:
        primitives: The reflex primitives wrapper to score with.
        rubric: The rubric applied to every verified output.
        name: Optional verifier name (defaults to ``"reflexive"``).
        config: Optional configuration dictionary.
    """

    def __init__(
        self,
        primitives: ReflexPrimitives,
        rubric: Rubric,
        name: str = "",
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(name=name or "reflexive", config=config)
        self.primitives = primitives
        self.rubric = rubric

    def verify(
        self,
        expected: Any,
        actual: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        """Score *actual* against the rubric and return a verdict.

        Args:
            expected: Reference value (unused by reflex scoring, kept for
                interface compatibility).
            actual: The value produced by the agent under test.
            context: Optional additional context.

        Returns:
            A :class:`~harness.core.types.VerificationResult` with the
            normalised score and reflex detail.
        """
        actual_text = "" if actual is None else str(actual)
        result = self.primitives.score_gate(actual_text, self.rubric)
        raw_score = float(result.value)
        normalized = round(raw_score / 10.0, 4)
        passes = raw_score < self.rubric.pass_threshold

        return VerificationResult(
            verdict=Verdict.PASS if passes else Verdict.FAIL,
            score=normalized,
            details={
                "rubric_id": self.rubric.rubric_id,
                "rubric_name": self.rubric.name,
                "rubric_version": self.rubric.version,
                "raw_score": raw_score,
                "pass_threshold": self.rubric.pass_threshold,
                "escalate": result.escalate,
                "latency_ms": result.latency_ms,
            },
            feedback=(
                f"Reflex score {raw_score}/10 "
                f"({'below' if passes else 'at or above'} pass threshold "
                f"{self.rubric.pass_threshold})."
            ),
        )
