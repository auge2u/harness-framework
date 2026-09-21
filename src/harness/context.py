"""Run-time execution context for harness operations."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from harness.core.types import ChangeScope


# ---------------------------------------------------------------------------
# Dynamic scoping table -- maps change types to required evidence and gates
# ---------------------------------------------------------------------------

# Each entry: (change_description, required_evidence_list, gate_type)
# The surface_tag is used to match against changed surface names.
_DYNAMIC_SCOPING_RULES: List[Dict[str, Any]] = [
    {
        "surface_tags": ["instructions", "instruction"],
        "description": "Instruction fragment edit",
        "required_evidence": ["held-in non-regression", "held-out non-regression"],
        "gate_type": "automated",
    },
    {
        "surface_tags": ["tools"],
        "description": "Tool descriptor change",
        "required_evidence": [
            "pass-rate",
            "safety review",
            "cost check",
        ],
        "gate_type": "automated+safety_gate",
    },
    {
        "surface_tags": ["tool_addition", "tool_removal"],
        "description": "Tool addition or removal",
        "required_evidence": [
            "pass-rate",
            "safety review",
            "adversarial test",
        ],
        "gate_type": "automated+safety_gate",
    },
    {
        "surface_tags": ["sandbox"],
        "description": "Sandbox policy relaxation",
        "required_evidence": [
            "pass-rate",
            "adversarial tests",
            "human approval",
        ],
        "gate_type": "human-in-the-loop",
    },
    {
        "surface_tags": ["memory"],
        "description": "Memory schema change",
        "required_evidence": [
            "pass-rate",
            "retrieval quality",
            "reproducibility check",
        ],
        "gate_type": "extended",
    },
    {
        "surface_tags": ["model_defaults", "routing", "backend"],
        "description": "Backend or routing change",
        "required_evidence": [
            "latency/cost/accuracy Pareto check",
        ],
        "gate_type": "pareto",
    },
    {
        "surface_tags": ["skills", "distillation"],
        "description": "Skill distillation",
        "required_evidence": [
            "reuse rate",
            "success on similar tasks",
        ],
        "gate_type": "automated",
    },
    {
        "surface_tags": ["config", "structural"],
        "description": "Structural or config patch",
        "required_evidence": [
            "all baseline scenarios",
            "held-out",
            "determinism",
        ],
        "gate_type": "strict",
    },
]


def _resolve_gate_type(gate_type_str: str) -> str:
    """Normalise a composite gate type string to a single gate type.

    The dynamic scoping table uses composite gate types like
    ``"automated+safety_gate"``.  This function maps them to the
    canonical gate type used by :class:`ExecutionScope`.

    Args:
        gate_type_str: Raw gate type string from the scoping table.

    Returns:
        One of: ``automated``, ``safety_gate``, ``human_in_the_loop``,
        ``extended``, ``pareto``, ``strict``.
    """
    if "human-in-the-loop" in gate_type_str:
        return "human_in_the_loop"
    if "pareto" in gate_type_str:
        return "pareto"
    if "extended" in gate_type_str:
        return "extended"
    if "strict" in gate_type_str:
        return "strict"
    if "safety_gate" in gate_type_str:
        return "safety_gate"
    return "automated"


@dataclass
class ExecutionScope:
    """Defines the scope of a harness operation.

    Attributes:
        change_scope: Classification of the change's impact radius.
        affected_surfaces: List of surface names touched by the change.
        required_evidence: Evidence categories that must be collected
            before the change can proceed.
        gate_type: Approval mechanism -- ``automated``,
            ``safety_gate``, ``human_in_the_loop``, ``extended``,
            ``pareto``, or ``strict``.
    """

    change_scope: ChangeScope = field(default=ChangeScope.ATOMIC)
    affected_surfaces: List[str] = field(default_factory=list)
    required_evidence: List[str] = field(default_factory=list)
    gate_type: str = "automated"  # automated | safety_gate | human_in_the_loop | extended | pareto | strict


def _build_env_snapshot() -> Dict[str, str]:
    """Capture a snapshot of the Python execution environment.

    Returns:
        Dict with ``python_version``, ``platform``, and selected
        environment variables relevant for reproducibility.
    """
    snapshot: Dict[str, str] = {
        "python_version": sys.version,
        "platform": sys.platform,
    }
    # Capture harness-relevant env vars.
    for key in (
        "HARNESS_ENV",
        "HARNESS_DEBUG",
        "HARNESS_DISABLE_TELEMETRY",
        "HARNESS_COST_BUDGET",
        "PYTHONHASHSEED",
    ):
        val = os.environ.get(key)
        if val is not None:
            snapshot[key] = val
    return snapshot


@dataclass
class RunContext:
    """Full execution context for a harness run.

    Attributes:
        tenant: Tenant identifier for multi-tenant deployments.
        run_id: Unique identifier for this run.
        user_id: Optional user identifier that initiated the run.
        session_id: Optional session identifier.
        lineage_version: Version of the lineage store in use.
        policy_constraints: Policy constraints to enforce during the run.
        scope: Computed :class:`ExecutionScope` for this run.
        metadata: Arbitrary run metadata.
        env_snapshot: Snapshot of the Python environment at run start.
        random_seed: Optional fixed random seed for reproducibility.
    """

    tenant: str = "default"
    run_id: str = ""
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    lineage_version: Optional[str] = None
    policy_constraints: Dict[str, Any] = field(default_factory=dict)
    scope: ExecutionScope = field(default_factory=ExecutionScope)
    metadata: Dict[str, Any] = field(default_factory=dict)
    env_snapshot: Dict[str, str] = field(default_factory=_build_env_snapshot)
    random_seed: Optional[int] = None

    def get_scope_for_change(
        self, changed_surfaces: List[str]
    ) -> ExecutionScope:
        """Determine the execution scope and required evidence for a change.

        Uses the dynamic scoping table to map the set of changed surfaces
        to the appropriate :class:`ChangeScope`, required evidence list,
        and gate type.

        Args:
            changed_surfaces: List of surface names that were modified.

        Returns:
            An :class:`ExecutionScope` fully populated with the matched
            scoping rules.
        """
        if not changed_surfaces:
            return ExecutionScope(
                change_scope=ChangeScope.SYSTEM,
                affected_surfaces=[],
                required_evidence=["full regression suite"],
                gate_type="strict",
            )

        # De-duplicate while preserving order.
        unique_surfaces = list(dict.fromkeys(changed_surfaces))

        # Determine change scope based on number and connectivity of surfaces.
        if len(unique_surfaces) == 1:
            change_scope = ChangeScope.ATOMIC
        else:
            # For multi-surface changes, default to COMPONENT unless we can
            # determine otherwise.  Callers with access to a HarnessConfig
            # can use HarnessConfig.get_scope_for_change for more precise
            # graph-based scope determination.
            change_scope = ChangeScope.COMPONENT

        # Match surfaces against the scoping table.
        all_evidence: List[str] = []
        gate_types: List[str] = []
        matched_rules: List[str] = []

        lower_surfaces = [s.lower() for s in unique_surfaces]

        for rule in _DYNAMIC_SCOPING_RULES:
            tags = [t.lower() for t in rule["surface_tags"]]
            if any(tag in lower_surfaces for tag in tags):
                all_evidence.extend(rule["required_evidence"])
                gate_types.append(_resolve_gate_type(rule["gate_type"]))
                matched_rules.append(rule["description"])

        # Deduplicate evidence preserving order.
        seen_evidence: set = set()
        deduped_evidence: List[str] = []
        for ev in all_evidence:
            if ev not in seen_evidence:
                seen_evidence.add(ev)
                deduped_evidence.append(ev)

        # Determine final gate type -- use the most restrictive one.
        gate_priority = [
            "strict",
            "human_in_the_loop",
            "extended",
            "pareto",
            "safety_gate",
            "automated",
        ]
        final_gate = "automated"
        if gate_types:
            for priority_gate in gate_priority:
                if priority_gate in gate_types:
                    final_gate = priority_gate
                    break

        # If no rules matched, use a safe default.
        if not deduped_evidence:
            deduped_evidence = ["basic regression check"]

        return ExecutionScope(
            change_scope=change_scope,
            affected_surfaces=unique_surfaces,
            required_evidence=deduped_evidence,
            gate_type=final_gate,
        )
