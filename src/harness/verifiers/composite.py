"""Composite verifier that aggregates results from multiple verifiers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from harness.core.types import Verdict, VerificationResult
from harness.verifiers.base import Verifier


class CompositeVerifier(Verifier):
    """Aggregates results from multiple child verifiers.

    The aggregation strategy is controlled by *mode*:

    * ``"all"``      — score is the **minimum** of all scores;
      verdict is ``PASS`` only if **all** children pass.
    * ``"any"``      — score is the **maximum** of all scores;
      verdict is ``PASS`` if **any** child passes.
    * ``"weighted"`` — score is the weighted average;
      verdict is ``PASS`` if the weighted score is ``>= 0.5``.

    Args:
        verifiers: Ordered list of child verifier instances.
        mode: Aggregation mode (``"all"``, ``"any"``, or ``"weighted"``).
        weights: Per-child weights for ``"weighted"`` mode.  If ``None``,
            uniform weights are used.
        name: Optional name for this verifier instance.
        config: Optional configuration dictionary.

    Raises:
        ValueError: If *mode* is unsupported or if *weights* length does
            not match *verifiers* length in ``"weighted"`` mode.
    """

    def __init__(
        self,
        verifiers: List[Verifier],
        mode: str = "all",
        weights: Optional[List[float]] = None,
        name: str = "",
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(name=name or f"composite_{mode}", config=config)
        if not verifiers:
            raise ValueError("CompositeVerifier requires at least one child verifier")
        self._verifiers = list(verifiers)
        self._mode = mode
        self._weights: Optional[List[float]] = None

        if mode not in {"all", "any", "weighted"}:
            raise ValueError(f"Unsupported mode: {mode!r}. Use 'all', 'any', or 'weighted'.")

        if mode == "weighted":
            if weights is not None:
                if len(weights) != len(verifiers):
                    raise ValueError(
                        f"weights length ({len(weights)}) must match "
                        f"verifiers length ({len(verifiers)})"
                    )
                self._weights = [float(w) for w in weights]
            else:
                n = len(verifiers)
                self._weights = [1.0 / n] * n

    def verify(
        self,
        expected: Any,
        actual: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        """Run all child verifiers and aggregate their results."""
        child_results: List[VerificationResult] = []
        for verifier in self._verifiers:
            try:
                result = verifier.verify(expected, actual, context)
            except Exception as exc:
                result = VerificationResult(
                    verdict=Verdict.FAIL,
                    score=0.0,
                    details={"error": str(exc)},
                    feedback=f"Verifier '{verifier.name}' raised an exception: {exc}",
                )
            child_results.append(result)

        scores = [r.score for r in child_results]
        verdicts = [r.verdict for r in child_results]

        # Compute aggregated score
        if self._mode == "all":
            score = min(scores) if scores else 0.0
        elif self._mode == "any":
            score = max(scores) if scores else 0.0
        else:  # weighted
            if self._weights is None:
                n = len(scores)
                weights = [1.0 / n] * n if n > 0 else []
            else:
                weights = self._weights
            total_weight = sum(weights)
            if total_weight == 0:
                score = 0.0
            else:
                score = sum(w * s for w, s in zip(weights, scores)) / total_weight

        # Compute aggregated verdict
        if self._mode == "all":
            verdict = Verdict.PASS if all(v == Verdict.PASS for v in verdicts) else Verdict.FAIL
        elif self._mode == "any":
            verdict = Verdict.PASS if any(v == Verdict.PASS for v in verdicts) else Verdict.FAIL
        else:  # weighted
            verdict = Verdict.PASS if score >= 0.5 else Verdict.FAIL

        return VerificationResult(
            verdict=verdict,
            score=score,
            details={
                "mode": self._mode,
                "weights": self._weights,
                "child_results": [
                    {
                        "name": v.name,
                        "verdict": r.verdict.name,
                        "score": r.score,
                        "feedback": r.feedback,
                    }
                    for v, r in zip(self._verifiers, child_results)
                ],
            },
            feedback=f"Composite ({self._mode}) score: {score:.4f}",
        )
