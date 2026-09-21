"""Harness reflex layer — millisecond-latency System 1 probabilistic tier.

This package implements the "Jev primitives" pattern (bool / score /
choice zero-text classification) natively inside the Harness Framework:

* :mod:`harness.reflex.backend` — reflex backend ABC, deterministic mock,
  and a TypeSafe-Jev-compatible HTTP client with graceful fallback.
* :mod:`harness.reflex.rubrics` — versioned qualitative scoring rubrics.
* :mod:`harness.reflex.primitives` — bool/score/choice gates with
  escalation logic baked in.
* :mod:`harness.reflex.escalation` — System 1 -> System 2 handoff policy
  with statistics.
* :mod:`harness.reflex.verifier` — reflex verifier for the verifier
  framework.
* :mod:`harness.reflex.linter` — 6-rule qualitative code audit.
* :mod:`harness.reflex.router` — System 1 skill/tool router.
* :mod:`harness.reflex.gates` — reflex acceptance gate.
* :mod:`harness.reflex.ci` — closed-loop CI scan/remediate/re-verify
  runner.
"""

from __future__ import annotations

from harness.reflex.backend import JevBackend, MockReflexBackend, ReflexBackend
from harness.reflex.escalation import EscalationEvent, EscalationPolicy
from harness.reflex.gates import ReflexGate
from harness.reflex.linter import (
    COMMENT_QUALITY_OPTIONS,
    DIFF_RISK_RUBRIC,
    N_PLUS_ONE_RUBRIC,
    OWASP_CHOICE_OPTIONS,
    OWASP_SEVERITY_RUBRIC,
    SIDE_EFFECT_RUBRIC,
    QualitativeLinter,
)
from harness.reflex.meta import DecisionRecord, MetaRefinementAnalyzer, RubricMetrics
from harness.reflex.primitives import ReflexPrimitives, ReflexResult
from harness.reflex.router import MAX_SKILLS, RoutableSkill, ReflexRouter, RoutingDecision
from harness.reflex.rubrics import Rubric, RubricRegistry
from harness.reflex.verifier import ReflexiveVerifier

def __getattr__(name: str):  # PEP 562 — lazy ci imports
    """Lazily expose ``harness.reflex.ci`` symbols.

    The ``ci`` module doubles as a ``python -m harness.reflex.ci`` entry
    point; importing it eagerly here would put it in ``sys.modules`` before
    runpy executes it and trigger a RuntimeWarning. Lazy loading keeps the
    public API identical while avoiding the warning.
    """
    if name in ("AuditFinding", "ReflexCIRunner", "default_remediate"):
        from harness.reflex import ci

        return getattr(ci, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Backends
    "ReflexBackend",
    "MockReflexBackend",
    "JevBackend",
    # Rubrics
    "Rubric",
    "RubricRegistry",
    # Primitives
    "ReflexResult",
    "ReflexPrimitives",
    # Escalation
    "EscalationEvent",
    "EscalationPolicy",
    # Meta-refinement
    "DecisionRecord",
    "RubricMetrics",
    "MetaRefinementAnalyzer",
    # Verifier
    "ReflexiveVerifier",
    # Linter
    "QualitativeLinter",
    "SIDE_EFFECT_RUBRIC",
    "DIFF_RISK_RUBRIC",
    "N_PLUS_ONE_RUBRIC",
    "OWASP_SEVERITY_RUBRIC",
    "COMMENT_QUALITY_OPTIONS",
    "OWASP_CHOICE_OPTIONS",
    # Router
    "RoutableSkill",
    "RoutingDecision",
    "ReflexRouter",
    "MAX_SKILLS",
    # Gates
    "ReflexGate",
    # CI
    "AuditFinding",
    "ReflexCIRunner",
    "default_remediate",
]
