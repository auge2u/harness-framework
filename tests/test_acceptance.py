"""Tests for the harness.acceptance module."""
from __future__ import annotations

import pytest

from harness.acceptance import (
    AcceptanceSuite,
    CostGate,
    DeterminismGate,
    DiffScopeGate,
    GateReport,
    GateResult,
    RegressionGate,
    RollbackGate,
    SecurityGate,
    TraceabilityGate,
)
from harness.patch import HarnessPatch, PatchOperation
from harness.policy import (
    PrivilegeEscalationType,
    PrivilegeMonotonicityCheck,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_patch_with_inverse():
    """Create a patch with an auto-generated inverse."""
    return HarnessPatch(
        patch_id="p1",
        surface="backend",
        target_id="model",
        operation=PatchOperation.REPLACE,
        before="old",
        after="new",
    )


# ---------------------------------------------------------------------------
# GateResult enum
# ---------------------------------------------------------------------------

def test_gate_result_values():
    """GateResult enum has correct values."""
    assert GateResult.PASS.value == "pass"
    assert GateResult.FAIL.value == "fail"
    assert GateResult.WARNING.value == "warning"
    assert GateResult.BLOCKED.value == "blocked"


def test_gate_result_members():
    """GateResult has exactly 4 members."""
    assert len(GateResult) == 4


# ---------------------------------------------------------------------------
# GateReport
# ---------------------------------------------------------------------------

def test_gate_report_creation():
    """GateReport can be created."""
    report = GateReport(
        gate_name="test",
        result=GateResult.PASS,
        message="All good",
        details={"score": 0.95},
    )
    assert report.gate_name == "test"
    assert report.result == GateResult.PASS
    assert report.message == "All good"
    assert report.details == {"score": 0.95}


def test_gate_report_defaults():
    """GateReport requires gate_name and result."""
    report = GateReport(gate_name="test", result=GateResult.PASS)
    assert report.message == ""
    assert report.details == {}


# ---------------------------------------------------------------------------
# RegressionGate
# ---------------------------------------------------------------------------

def test_regression_gate_pass():
    """RegressionGate passes when both deltas are positive."""
    gate = RegressionGate()
    report = gate.evaluate(None, None, {
        "baseline_score": 0.5,
        "candidate_score": 0.8,
        "held_out_baseline_score": 0.5,
        "held_out_candidate_score": 0.8,
    })
    assert report.result == GateResult.PASS
    assert "Improvement" in report.message


def test_regression_gate_warning():
    """RegressionGate warns when deltas are within tolerance."""
    gate = RegressionGate()
    report = gate.evaluate(None, None, {
        "baseline_score": 0.5,
        "candidate_score": 0.48,
        "held_out_baseline_score": 0.5,
        "held_out_candidate_score": 0.49,
    })
    assert report.result == GateResult.WARNING
    assert "Neutral" in report.message


def test_regression_gate_fail():
    """RegressionGate fails when any delta is below tolerance."""
    gate = RegressionGate()
    report = gate.evaluate(None, None, {
        "baseline_score": 0.8,
        "candidate_score": 0.5,
        "held_out_baseline_score": 0.8,
        "held_out_candidate_score": 0.5,
    })
    assert report.result == GateResult.FAIL
    assert "Regression" in report.message


# ---------------------------------------------------------------------------
# DiffScopeGate
# ---------------------------------------------------------------------------

def test_diff_scope_gate_pass():
    """DiffScopeGate passes when surface is editable."""
    gate = DiffScopeGate()
    patch = HarnessPatch(
        patch_id="p1", surface="backend", target_id="model",
        operation=PatchOperation.ADD, before=None, after="test",
    )
    report = gate.evaluate(patch, None, {
        "editable_surfaces": ["backend", "tool"],
    })
    assert report.result == GateResult.PASS


def test_diff_scope_gate_blocked():
    """DiffScopeGate blocks when surface is not editable."""
    gate = DiffScopeGate()
    patch = HarnessPatch(
        patch_id="p1", surface="secret", target_id="key",
        operation=PatchOperation.ADD, before=None, after="test",
    )
    report = gate.evaluate(patch, None, {
        "editable_surfaces": ["backend", "tool"],
    })
    assert report.result == GateResult.BLOCKED


# ---------------------------------------------------------------------------
# SecurityGate
# ---------------------------------------------------------------------------

def test_security_gate_pass_no_check():
    """SecurityGate passes when no privilege check provided."""
    gate = SecurityGate()
    report = gate.evaluate(None, None, {})
    assert report.result == GateResult.PASS


def test_security_gate_pass_safe():
    """SecurityGate passes when privilege check is safe."""
    gate = SecurityGate()
    check = PrivilegeMonotonicityCheck(
        escalation_type=PrivilegeEscalationType.NONE,
        is_escalation=False,
    )
    report = gate.evaluate(None, None, {"privilege_check": check})
    assert report.result == GateResult.PASS


def test_security_gate_blocked():
    """SecurityGate blocks when escalation detected."""
    gate = SecurityGate()
    check = PrivilegeMonotonicityCheck(
        escalation_type=PrivilegeEscalationType.SANDBOX_RELAX,
        is_escalation=True,
        details="sandbox relaxation detected",
    )
    report = gate.evaluate(None, None, {"privilege_check": check})
    assert report.result == GateResult.BLOCKED


# ---------------------------------------------------------------------------
# TraceabilityGate
# ---------------------------------------------------------------------------

def test_traceability_gate_pass():
    """TraceabilityGate passes when all required fields present."""
    gate = TraceabilityGate()
    report = gate.evaluate(None, None, {
        "parent_version": "1.0.0",
        "patch": "p1",
        "baseline_score": 0.9,
        "scenarios_run": ["s1", "s2"],
    })
    assert report.result == GateResult.PASS


def test_traceability_gate_fail():
    """TraceabilityGate fails when fields missing."""
    gate = TraceabilityGate()
    report = gate.evaluate(None, None, {"parent_version": "1.0.0"})
    assert report.result == GateResult.FAIL
    assert "Missing" in report.message


def test_traceability_gate_empty_fields():
    """TraceabilityGate fails when required fields are empty."""
    gate = TraceabilityGate()
    report = gate.evaluate(None, None, {
        "parent_version": "1.0.0",
        "patch": "p1",
        "baseline_score": 0.0,
        "scenarios_run": [],
    })
    assert report.result == GateResult.FAIL


# ---------------------------------------------------------------------------
# RollbackGate
# ---------------------------------------------------------------------------

def test_rollback_gate_pass():
    """RollbackGate passes when patch has inverse."""
    gate = RollbackGate()
    patch = _make_patch_with_inverse()
    report = gate.evaluate(patch, None, {})
    assert report.result == GateResult.PASS
    assert "Inverse patch available" in report.message


def test_rollback_gate_fail():
    """RollbackGate fails when patch has no inverse."""
    gate = RollbackGate()
    report = gate.evaluate(None, None, {})
    assert report.result == GateResult.FAIL


# ---------------------------------------------------------------------------
# CostGate
# ---------------------------------------------------------------------------

def test_cost_gate_pass():
    """CostGate passes when under budget."""
    gate = CostGate()
    report = gate.evaluate(None, None, {"cost_usd": 5.0, "budget_usd": 10.0})
    assert report.result == GateResult.PASS


def test_cost_gate_fail():
    """CostGate fails when over budget."""
    gate = CostGate()
    report = gate.evaluate(None, None, {"cost_usd": 15.0, "budget_usd": 10.0})
    assert report.result == GateResult.FAIL


def test_cost_gate_infinite_budget():
    """CostGate passes with infinite budget."""
    gate = CostGate()
    report = gate.evaluate(None, None, {"cost_usd": 999999.0})
    assert report.result == GateResult.PASS


# ---------------------------------------------------------------------------
# DeterminismGate
# ---------------------------------------------------------------------------

def test_determinism_gate_pass():
    """DeterminismGate passes with low variance."""
    gate = DeterminismGate()
    report = gate.evaluate(None, None, {
        "score_variance": 0.05,
        "variance_threshold": 0.1,
    })
    assert report.result == GateResult.PASS


def test_determinism_gate_warning():
    """DeterminismGate warns with high variance."""
    gate = DeterminismGate()
    report = gate.evaluate(None, None, {
        "score_variance": 0.5,
        "variance_threshold": 0.1,
    })
    assert report.result == GateResult.WARNING


def test_determinism_gate_zero_variance():
    """DeterminismGate passes with zero variance."""
    gate = DeterminismGate()
    report = gate.evaluate(None, None, {
        "score_variance": 0.0,
        "variance_threshold": 0.1,
    })
    assert report.result == GateResult.PASS


# ---------------------------------------------------------------------------
# AcceptanceSuite
# ---------------------------------------------------------------------------

def test_suite_evaluate_all_returns_list():
    """evaluate_all returns a list of GateReports."""
    suite = AcceptanceSuite.default_suite()
    patch = _make_patch_with_inverse()
    reports = suite.evaluate_all(patch, None, {
        "baseline_score": 0.5,
        "candidate_score": 0.8,
        "held_out_baseline_score": 0.5,
        "held_out_candidate_score": 0.8,
        "editable_surfaces": ["backend"],
        "cost_usd": 5.0,
        "budget_usd": 10.0,
        "parent_version": "1.0.0",
        "patch": patch,
        "scenarios_run": ["s1"],
        "score_variance": 0.05,
        "variance_threshold": 0.1,
    })
    assert isinstance(reports, list)
    assert len(reports) == 7
    assert all(isinstance(r, GateReport) for r in reports)


def test_suite_can_promote_all_pass():
    """can_promote returns True when no blocking reports."""
    suite = AcceptanceSuite.default_suite()
    patch = _make_patch_with_inverse()
    reports = suite.evaluate_all(patch, None, {
        "baseline_score": 0.5,
        "candidate_score": 0.8,
        "held_out_baseline_score": 0.5,
        "held_out_candidate_score": 0.8,
        "editable_surfaces": ["backend"],
        "cost_usd": 5.0,
        "budget_usd": 10.0,
        "parent_version": "1.0.0",
        "patch": patch,
        "scenarios_run": ["s1"],
        "score_variance": 0.05,
        "variance_threshold": 0.1,
    })
    assert suite.can_promote(reports) is True


def test_suite_can_promote_blocked():
    """can_promote returns False when a gate is blocked."""
    suite = AcceptanceSuite.default_suite()
    patch = HarnessPatch(
        patch_id="p1", surface="secret", target_id="key",
        operation=PatchOperation.ADD, before=None, after="test",
    )
    reports = suite.evaluate_all(patch, None, {
        "baseline_score": 0.5,
        "candidate_score": 0.8,
        "held_out_baseline_score": 0.5,
        "held_out_candidate_score": 0.8,
        "editable_surfaces": ["backend"],
        "cost_usd": 5.0,
        "budget_usd": 10.0,
        "parent_version": "1.0.0",
        "scenarios_run": ["s1"],
    })
    assert suite.can_promote(reports) is False


def test_suite_get_blocking_reports():
    """get_blocking_reports returns only blocking reports."""
    suite = AcceptanceSuite.default_suite()
    patch = HarnessPatch(
        patch_id="p1", surface="secret", target_id="key",
        operation=PatchOperation.ADD, before=None, after="test",
    )
    reports = suite.evaluate_all(patch, None, {
        "baseline_score": 0.5,
        "candidate_score": 0.8,
        "held_out_baseline_score": 0.5,
        "held_out_candidate_score": 0.8,
        "editable_surfaces": ["backend"],
        "cost_usd": 5.0,
        "budget_usd": 10.0,
        "parent_version": "1.0.0",
        "scenarios_run": ["s1"],
    })
    blocking = suite.get_blocking_reports(reports)
    assert len(blocking) >= 1
    assert all(r.result in (GateResult.FAIL, GateResult.BLOCKED) for r in blocking)


def test_suite_empty_reports_cannot_promote():
    """can_promote returns False for empty reports."""
    suite = AcceptanceSuite()
    assert suite.can_promote([]) is False
