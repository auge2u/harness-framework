"""Reflex acceptance gate: fast System 1 checks before expensive gates."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

from harness.acceptance import AcceptanceGate, GateReport, GateResult
from harness.reflex.primitives import ReflexResult

__all__ = ["ReflexGate"]

#: A reflex check maps the gate evidence dictionary to a ReflexResult.
ReflexCheck = Callable[[Dict[str, Any]], ReflexResult]


class ReflexGate(AcceptanceGate):
    """Acceptance gate that runs fast reflex checks before expensive gates.

    Configured with a list of ``(name, check)`` pairs where *check* is a
    callable receiving the gate evidence dictionary and returning a
    :class:`~harness.reflex.primitives.ReflexResult`.  :meth:`evaluate`
    runs every check:

    * ``PASS`` when all checks pass (no check requests a block).
    * ``FAIL`` when any check requests a block (``result.escalate``).

    Results needing escalation are marked in the gate report metadata
    under ``details["escalations"]`` so downstream System 2 gates can
    pick them up.

    Args:
        checks: Ordered list of ``(name, callable)`` reflex checks.
        name: Optional gate name (defaults to ``"reflex"``).
        description: Optional gate description.
    """

    def __init__(
        self,
        checks: List[Tuple[str, ReflexCheck]],
        name: str = "",
        description: str = "",
    ) -> None:
        self.name = name or "reflex"
        self.description = description or "Fast System 1 reflex checks"
        self.checks: List[Tuple[str, ReflexCheck]] = list(checks)

    def evaluate(
        self, patch: Any, context: Any, evidence: Dict[str, Any]
    ) -> GateReport:
        """Run all reflex checks and aggregate the outcome."""
        evidence = evidence if isinstance(evidence, dict) else {}
        check_results: Dict[str, Any] = {}
        blocks: List[str] = []
        escalations: List[Dict[str, Any]] = []

        for check_name, check in self.checks:
            result = check(evidence)
            check_results[check_name] = {
                "primitive": result.primitive,
                "value": result.value,
                "confidence": result.confidence,
                "latency_ms": result.latency_ms,
                "escalate": result.escalate,
            }
            if result.escalate:
                blocks.append(check_name)
                escalations.append(
                    {
                        "check": check_name,
                        "primitive": result.primitive,
                        "value": result.value,
                        "confidence": result.confidence,
                    }
                )

        details: Dict[str, Any] = {
            "checks": check_results,
            "escalations": escalations,
            "check_count": len(self.checks),
        }

        if not blocks:
            return GateReport(
                self.name,
                GateResult.PASS,
                f"All {len(self.checks)} reflex checks passed.",
                details,
            )

        return GateReport(
            self.name,
            GateResult.FAIL,
            f"Reflex checks requested a block: {', '.join(blocks)}.",
            details,
        )
