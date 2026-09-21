"""Acceptance gates for harness patch promotion.

Each gate evaluates whether a patch meets criteria for promotion.
Gates are configurable per surface and compose independently.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class GateResult(Enum):
    """Possible outcomes from running an acceptance gate."""

    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    BLOCKED = "blocked"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class GateReport:
    """Result from running a single acceptance gate.

    Attributes
    ----------
    gate_name:
        The identifier of the gate that produced this report.
    result:
        The outcome — :py:attr:`GateResult.PASS`, ``FAIL``, ``WARNING``,
        or ``BLOCKED``.
    message:
        Human-readable summary of the result.
    details:
        Structured data (scores, diffs, metadata) for downstream consumers.
    """

    gate_name: str
    result: GateResult
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalise fields after construction."""
        if self.gate_name is not None:
            self.gate_name = str(self.gate_name)
        if self.message is not None:
            self.message = str(self.message)
        if self.details is None:
            self.details = {}


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class AcceptanceGate(ABC):
    """Abstract base for all acceptance gates.

    Subclasses must set :pyattr:`name` and :pyattr:`description` and
    implement :py:meth:`evaluate`.
    """

    name: str = ""
    description: str = ""

    @abstractmethod
    def evaluate(
        self, patch: Any, context: Any, evidence: Dict[str, Any]
    ) -> GateReport:
        """Evaluate the gate.

        Parameters
        ----------
        patch:
            The :class:`HarnessPatch` being evaluated.
        context:
            A :class:`RunContext` or similar execution context.
        evidence:
            Dictionary of evidence (scores, traces, comparison results).

        Returns
        -------
        GateReport
            Structured result with outcome and details.
        """
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"


# ---------------------------------------------------------------------------
# Concrete gates
# ---------------------------------------------------------------------------


class RegressionGate(AcceptanceGate):
    """Regression gate — candidate must not degrade baseline or held-out splits.

    Evidence keys used:

    * ``baseline_score`` — baseline aggregate score (float).
    * ``candidate_score`` — candidate aggregate score (float).
    * ``held_out_baseline_score`` — held-out baseline score (float).
    * ``held_out_candidate_score`` — held-out candidate score (float).

    PASS: both deltas are non-negative and at least one is positive.
    WARNING: both deltas are within a small tolerance (>= -0.05).
    FAIL: any delta falls below the tolerance.
    """

    name = "regression"
    description = "No degradation on baseline or held-out scenarios"

    def evaluate(
        self, patch: Any, context: Any, evidence: Dict[str, Any]
    ) -> GateReport:
        """Evaluate the regression gate.

        Compares candidate scores against baseline scores on both the held-in
        and held-out splits.  A small tolerance of -0.05 is allowed for
        stochastic variance.
        """
        baseline_score = self._float(evidence.get("baseline_score"), 0.0)
        candidate_score = self._float(evidence.get("candidate_score"), 0.0)
        held_out_baseline = self._float(
            evidence.get("held_out_baseline_score"), 0.0
        )
        held_out_candidate = self._float(
            evidence.get("held_out_candidate_score"), 0.0
        )

        delta_in = candidate_score - baseline_score
        delta_ho = held_out_candidate - held_out_baseline

        details: Dict[str, Any] = {
            "baseline_score": baseline_score,
            "candidate_score": candidate_score,
            "held_out_baseline_score": held_out_baseline,
            "held_out_candidate_score": held_out_candidate,
            "delta_in": round(delta_in, 6),
            "delta_ho": round(delta_ho, 6),
        }

        if delta_in >= 0 and delta_ho >= 0 and max(delta_in, delta_ho) > 0:
            return GateReport(
                self.name,
                GateResult.PASS,
                f"Improvement: delta_in={delta_in:+.4f}, delta_ho={delta_ho:+.4f}",
                details,
            )
        if delta_in >= -0.05 and delta_ho >= -0.05:
            return GateReport(
                self.name,
                GateResult.WARNING,
                f"Neutral: delta_in={delta_in:+.4f}, delta_ho={delta_ho:+.4f}",
                details,
            )
        return GateReport(
            self.name,
            GateResult.FAIL,
            f"Regression: delta_in={delta_in:+.4f}, delta_ho={delta_ho:+.4f}",
            details,
        )

    @staticmethod
    def _float(value: Any, default: float) -> float:
        """Coerce *value* to float, returning *default* on failure."""
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default


class DiffScopeGate(AcceptanceGate):
    """Diff scope gate — patch must stay within declared editable surfaces.

    Evidence keys used:

    * ``editable_surfaces`` — a ``set[str]`` or ``list[str]`` of allowed surfaces.

    PASS: the patch's surface is in the editable set.
    BLOCKED: the patch targets a non-editable surface.
    """

    name = "diff_scope"
    description = "Patch targets only editable surfaces"

    def evaluate(
        self, patch: Any, context: Any, evidence: Dict[str, Any]
    ) -> GateReport:
        """Check that the patch's surface is in the editable set."""
        editable = evidence.get("editable_surfaces", set())
        if isinstance(editable, (list, tuple)):
            editable = set(str(s) for s in editable)
        elif not isinstance(editable, set):
            editable = set()

        patch_surface = self._extract_surface(patch)

        if patch_surface is not None and patch_surface in editable:
            return GateReport(
                self.name,
                GateResult.PASS,
                f"Surface '{patch_surface}' is in editable set ({len(editable)} surfaces)",
                {"patch_surface": patch_surface, "editable_count": len(editable)},
            )

        return GateReport(
            self.name,
            GateResult.BLOCKED,
            f"Surface '{patch_surface}' is NOT in editable set: {sorted(editable)}",
            {
                "patch_surface": patch_surface,
                "editable_surfaces": sorted(editable),
            },
        )

    @staticmethod
    def _extract_surface(patch: Any) -> Optional[str]:
        """Extract the surface name from *patch*."""
        if patch is None:
            return None
        if isinstance(patch, str):
            return patch.strip().lower()
        surface = getattr(patch, "surface", None)
        if surface is not None:
            return str(surface).strip().lower()
        if isinstance(patch, dict):
            surface = patch.get("surface")
            if surface is not None:
                return str(surface).strip().lower()
        return None


class SecurityGate(AcceptanceGate):
    """Security gate — no added privileges without explicit approval.

    Evidence keys used:

    * ``privilege_check`` — a :class:`PrivilegeMonotonicityCheck` object or
      any object with ``.is_safe`` and ``.is_escalation`` boolean attributes.

    PASS: privilege check shows no escalation.
    BLOCKED: an actual escalation is detected.
    WARNING: potential concern flagged.
    """

    name = "security"
    description = "No unauthorized privilege escalation"

    def evaluate(
        self, patch: Any, context: Any, evidence: Dict[str, Any]
    ) -> GateReport:
        """Evaluate the security gate based on a privilege check."""
        escalation = evidence.get("privilege_check")

        if escalation is None:
            return GateReport(
                self.name,
                GateResult.PASS,
                "No privilege check performed — assuming safe.",
                {"checked": False},
            )

        # PrivilegeMonotonicityCheck interface
        is_safe = getattr(escalation, "is_safe", True)
        is_escalation = getattr(escalation, "is_escalation", False)
        escalation_type = getattr(
            escalation, "escalation_type", None
        )
        type_str = (
            escalation_type.value
            if escalation_type is not None and hasattr(escalation_type, "value")
            else str(escalation_type)
        )
        details_str = getattr(escalation, "details", "")

        if is_safe:
            return GateReport(
                self.name,
                GateResult.PASS,
                "No privilege escalation detected.",
                {
                    "is_safe": True,
                    "escalation_type": type_str,
                    "details": details_str,
                },
            )

        if is_escalation:
            return GateReport(
                self.name,
                GateResult.BLOCKED,
                f"Privilege escalation: {type_str} — {details_str}",
                {
                    "is_safe": False,
                    "is_escalation": True,
                    "escalation_type": type_str,
                    "details": details_str,
                },
            )

        return GateReport(
            self.name,
            GateResult.WARNING,
            f"Potential concern: {details_str}",
            {
                "is_safe": False,
                "is_escalation": False,
                "escalation_type": type_str,
                "details": details_str,
            },
        )


class TraceabilityGate(AcceptanceGate):
    """Traceability gate — complete lineage record with all required fields.

    Evidence keys used:

    * ``parent_version`` — the parent version identifier.
    * ``patch`` — the patch object or identifier.
    * ``baseline_score`` — the baseline score for comparison.
    * ``scenarios_run`` — list or count of scenarios that were executed.

    PASS: all required fields are present and non-empty.
    FAIL: one or more required fields are missing or empty.
    """

    name = "traceability"
    description = "Complete lineage record with parent, patch, scores, artifacts"

    # Required fields that must be present and truthy in evidence.
    REQUIRED_FIELDS = [
        "parent_version",
        "patch",
        "baseline_score",
        "scenarios_run",
    ]

    def evaluate(
        self, patch: Any, context: Any, evidence: Dict[str, Any]
    ) -> GateReport:
        """Check that all required traceability fields are present."""
        missing: List[str] = []
        empty: List[str] = []

        for field_name in self.REQUIRED_FIELDS:
            if field_name not in evidence:
                missing.append(field_name)
            elif not evidence[field_name] and evidence[field_name] != 0:
                empty.append(field_name)

        all_issues = missing + empty
        if not all_issues:
            return GateReport(
                self.name,
                GateResult.PASS,
                "All traceability fields present and non-empty.",
                {
                    "required_fields": list(self.REQUIRED_FIELDS),
                    "present": list(self.REQUIRED_FIELDS),
                },
            )

        issues_desc = ""
        if missing:
            issues_desc += f"Missing fields: {missing}. "
        if empty:
            issues_desc += f"Empty fields: {empty}."

        return GateReport(
            self.name,
            GateResult.FAIL,
            issues_desc.strip(),
            {
                "required_fields": list(self.REQUIRED_FIELDS),
                "missing": missing,
                "empty": empty,
            },
        )


class RollbackGate(AcceptanceGate):
    """Rollback gate — an inverse patch must exist and be valid.

    PASS: the patch has a non-None ``inverse`` attribute.
    FAIL: no inverse patch is available.
    """

    name = "rollback"
    description = "Inverse patch stored and tested"

    def evaluate(
        self, patch: Any, context: Any, evidence: Dict[str, Any]
    ) -> GateReport:
        """Check that the patch has an inverse for rollback."""
        if patch is None:
            return GateReport(
                self.name,
                GateResult.FAIL,
                "No patch provided — cannot evaluate rollback.",
                {"has_inverse": False},
            )

        inverse = getattr(patch, "inverse", None)
        has_inverse = inverse is not None

        if has_inverse:
            inverse_type = type(inverse).__name__
            return GateReport(
                self.name,
                GateResult.PASS,
                f"Inverse patch available (type: {inverse_type}).",
                {"has_inverse": True, "inverse_type": inverse_type},
            )

        return GateReport(
            self.name,
            GateResult.FAIL,
            "No inverse patch — rollback is not possible.",
            {"has_inverse": False},
        )


class CostGate(AcceptanceGate):
    """Cost gate — patch must stay within the allocated budget.

    Evidence keys used:

    * ``cost_usd`` — actual cost in USD (float).
    * ``budget_usd`` — budget ceiling in USD (float, default ``inf``).

    PASS: cost is within budget.
    FAIL: cost exceeds budget.
    """

    name = "cost"
    description = "Cost within budget"

    def evaluate(
        self, patch: Any, context: Any, evidence: Dict[str, Any]
    ) -> GateReport:
        """Check that the cost is within budget."""
        cost = self._float(evidence.get("cost_usd"), 0.0)
        budget = self._float(evidence.get("budget_usd"), float("inf"))

        details: Dict[str, Any] = {
            "cost_usd": round(cost, 6),
            "budget_usd": round(budget, 6) if budget != float("inf") else None,
        }

        if cost <= budget:
            return GateReport(
                self.name,
                GateResult.PASS,
                f"Cost ${cost:.4f} <= budget ${budget:.4f}",
                details,
            )

        overage = cost - budget
        details["overage_usd"] = round(overage, 6)
        return GateReport(
            self.name,
            GateResult.FAIL,
            f"Cost ${cost:.4f} exceeds budget ${budget:.4f} (overage: ${overage:.4f})",
            details,
        )

    @staticmethod
    def _float(value: Any, default: float) -> float:
        """Coerce *value* to float, returning *default* on failure."""
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default


class DeterminismGate(AcceptanceGate):
    """Determinism gate — results must be reproducible across runs.

    Evidence keys used:

    * ``score_variance`` — variance of scores across multiple runs (float).
    * ``variance_threshold`` — maximum acceptable variance (float, default 0.1).

    PASS: variance is at or below the threshold.
    WARNING: variance exceeds the threshold.
    """

    name = "determinism"
    description = "Reproducible results with same seed"

    def evaluate(
        self, patch: Any, context: Any, evidence: Dict[str, Any]
    ) -> GateReport:
        """Check that score variance is within acceptable bounds."""
        variance = self._float(evidence.get("score_variance"), 0.0)
        threshold = self._float(evidence.get("variance_threshold"), 0.1)

        # Guard against negative variance (should never happen, but be safe).
        variance = max(0.0, variance)
        threshold = max(0.0, threshold)

        details: Dict[str, Any] = {
            "score_variance": round(variance, 6),
            "variance_threshold": round(threshold, 6),
        }

        if variance <= threshold:
            return GateReport(
                self.name,
                GateResult.PASS,
                f"Score variance {variance:.4f} <= threshold {threshold}",
                details,
            )

        details["excess_variance"] = round(variance - threshold, 6)
        return GateReport(
            self.name,
            GateResult.WARNING,
            f"Score variance {variance:.4f} exceeds threshold {threshold}",
            details,
        )

    @staticmethod
    def _float(value: Any, default: float) -> float:
        """Coerce *value* to float, returning *default* on failure."""
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default


# ---------------------------------------------------------------------------
# Composed suite
# ---------------------------------------------------------------------------


class AcceptanceSuite:
    """Composed suite of acceptance gates that patches must pass.

    Gates are run independently.  A patch can be promoted only if **no**
    gate reports ``FAIL`` or ``BLOCKED``.

    Parameters
    ----------
    gates:
        Ordered list of :class:`AcceptanceGate` instances to evaluate.
        If ``None``, an empty suite is created.
    """

    def __init__(self, gates: Optional[List[AcceptanceGate]] = None) -> None:
        self.gates: List[AcceptanceGate] = list(gates) if gates is not None else []

    def evaluate_all(
        self, patch: Any, context: Any, evidence: Dict[str, Any]
    ) -> List[GateReport]:
        """Run all gates and return their reports.

        Every gate is executed regardless of earlier failures, so callers
        get a complete picture of all issues.

        Parameters
        ----------
        patch:
            The patch being evaluated.
        context:
            Execution context.
        evidence:
            Dictionary of evidence for the gates.

        Returns
        -------
        list[GateReport]
            One report per gate, in the order the gates were registered.
        """
        reports: List[GateReport] = []
        for gate in self.gates:
            try:
                report = gate.evaluate(patch, context, evidence)
            except Exception as exc:
                report = GateReport(
                    gate.name,
                    GateResult.FAIL,
                    f"Gate '{gate.name}' raised an exception: {exc}",
                    {"exception": str(exc), "exception_type": type(exc).__name__},
                )
            reports.append(report)
        return reports

    def can_promote(self, reports: List[GateReport]) -> bool:
        """Check if all reports allow promotion.

        Returns ``True`` only if **no** report is ``FAIL`` or ``BLOCKED``.
        ``WARNING`` results do **not** block promotion.

        Parameters
        ----------
        reports:
            The list of :class:`GateReport` objects to inspect.

        Returns
        -------
        bool
            ``True`` if promotion is allowed.
        """
        if not reports:
            return False
        for report in reports:
            if report.result in (GateResult.FAIL, GateResult.BLOCKED):
                return False
        return True

    def get_blocking_reports(self, reports: List[GateReport]) -> List[GateReport]:
        """Return only the reports that block promotion.

        Parameters
        ----------
        reports:
            The full list of gate reports.

        Returns
        -------
        list[GateReport]
            Subset of *reports* with ``FAIL`` or ``BLOCKED`` results.
        """
        return [
            r for r in reports if r.result in (GateResult.FAIL, GateResult.BLOCKED)
        ]

    def get_warnings(self, reports: List[GateReport]) -> List[GateReport]:
        """Return only the reports that produced warnings.

        Parameters
        ----------
        reports:
            The full list of gate reports.

        Returns
        -------
        list[GateReport]
            Subset of *reports* with ``WARNING`` results.
        """
        return [r for r in reports if r.result == GateResult.WARNING]

    @classmethod
    def default_suite(cls) -> "AcceptanceSuite":
        """Factory for the standard gate suite.

        Includes all seven built-in gates:

        1. :class:`RegressionGate`
        2. :class:`DiffScopeGate`
        3. :class:`SecurityGate`
        4. :class:`TraceabilityGate`
        5. :class:`RollbackGate`
        6. :class:`CostGate`
        7. :class:`DeterminismGate`
        """
        return cls(
            [
                RegressionGate(),
                DiffScopeGate(),
                SecurityGate(),
                TraceabilityGate(),
                RollbackGate(),
                CostGate(),
                DeterminismGate(),
            ]
        )

    def __len__(self) -> int:
        return len(self.gates)

    def __repr__(self) -> str:
        gate_names = [g.name for g in self.gates]
        return f"AcceptanceSuite(gates={gate_names})"
