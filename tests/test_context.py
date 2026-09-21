"""Tests for the harness.context module."""
from __future__ import annotations

import pytest

from harness.context import ExecutionScope, RunContext
from harness.core.types import ChangeScope


# ---------------------------------------------------------------------------
# ExecutionScope defaults
# ---------------------------------------------------------------------------

def test_execution_scope_defaults():
    """ExecutionScope has correct defaults."""
    scope = ExecutionScope()
    assert scope.change_scope == ChangeScope.ATOMIC
    assert scope.affected_surfaces == []
    assert scope.required_evidence == []
    assert scope.gate_type == "automated"


def test_execution_scope_custom():
    """ExecutionScope can be fully customized."""
    scope = ExecutionScope(
        change_scope=ChangeScope.COMPONENT,
        affected_surfaces=["backend", "tool"],
        required_evidence=["pass-rate", "safety review"],
        gate_type="safety_gate",
    )
    assert scope.change_scope == ChangeScope.COMPONENT
    assert scope.affected_surfaces == ["backend", "tool"]
    assert scope.required_evidence == ["pass-rate", "safety review"]
    assert scope.gate_type == "safety_gate"


# ---------------------------------------------------------------------------
# ChangeScope (from core.types)
# ---------------------------------------------------------------------------

def test_change_scope_values():
    """ChangeScope enum has expected values."""
    assert ChangeScope.ATOMIC.value == 1
    assert ChangeScope.COMPONENT.value == 2
    assert ChangeScope.SYSTEM.value == 3


# ---------------------------------------------------------------------------
# RunContext creation
# ---------------------------------------------------------------------------

def test_run_context_defaults():
    """RunContext has correct default values."""
    ctx = RunContext()
    assert ctx.tenant == "default"
    assert ctx.run_id == ""
    assert ctx.user_id is None
    assert ctx.session_id is None
    assert ctx.lineage_version is None
    assert ctx.policy_constraints == {}
    assert isinstance(ctx.scope, ExecutionScope)
    assert ctx.metadata == {}
    assert ctx.random_seed is None


def test_run_context_custom():
    """RunContext can be fully customized."""
    ctx = RunContext(
        run_id="run-42",
        tenant="acme",
        user_id="user-1",
        session_id="sess-1",
        lineage_version="2.0.0",
        policy_constraints={"max_cost": 10.0},
        metadata={"env": "test"},
        random_seed=42,
    )
    assert ctx.run_id == "run-42"
    assert ctx.tenant == "acme"
    assert ctx.user_id == "user-1"
    assert ctx.session_id == "sess-1"
    assert ctx.lineage_version == "2.0.0"
    assert ctx.policy_constraints == {"max_cost": 10.0}
    assert ctx.metadata == {"env": "test"}
    assert ctx.random_seed == 42


# ---------------------------------------------------------------------------
# get_scope_for_change - single surface
# ---------------------------------------------------------------------------

def test_single_surface_atomic_scope():
    """Single surface change gets ATOMIC scope."""
    ctx = RunContext()
    result = ctx.get_scope_for_change(["backend"])
    assert result.change_scope == ChangeScope.ATOMIC
    assert "backend" in result.affected_surfaces


def test_single_instruction_surface():
    """Single instruction surface gets ATOMIC scope with automated gate."""
    ctx = RunContext()
    result = ctx.get_scope_for_change(["instructions"])
    assert result.change_scope == ChangeScope.ATOMIC


# ---------------------------------------------------------------------------
# get_scope_for_change - connected surfaces
# ---------------------------------------------------------------------------

def test_multiple_surfaces_component_scope():
    """Multiple surfaces get COMPONENT scope."""
    ctx = RunContext()
    result = ctx.get_scope_for_change(["backend", "tool", "routing"])
    assert result.change_scope == ChangeScope.COMPONENT
    assert len(result.affected_surfaces) == 3


# ---------------------------------------------------------------------------
# get_scope_for_change - sandbox / policy surfaces
# ---------------------------------------------------------------------------

def test_sandbox_surface_human_gate():
    """Sandbox surface triggers human-in-the-loop gate."""
    ctx = RunContext()
    result = ctx.get_scope_for_change(["sandbox"])
    assert result.gate_type == "human_in_the_loop"
    assert "human approval" in result.required_evidence


def test_tools_surface_safety_gate():
    """Tools surface triggers safety gate."""
    ctx = RunContext()
    result = ctx.get_scope_for_change(["tools"])
    assert "safety review" in result.required_evidence


def test_mixed_with_sandbox():
    """Mixed surfaces including sandbox get human-in-the-loop gate (most restrictive)."""
    ctx = RunContext()
    result = ctx.get_scope_for_change(["backend", "tool", "sandbox"])
    assert result.gate_type == "human_in_the_loop"


# ---------------------------------------------------------------------------
# get_scope_for_change - backend / routing surfaces
# ---------------------------------------------------------------------------

def test_backend_routing_pareto_gate():
    """Backend/routing surfaces trigger Pareto gate."""
    ctx = RunContext()
    result = ctx.get_scope_for_change(["model_defaults", "routing"])
    assert result.gate_type == "pareto"
    assert "latency/cost/accuracy Pareto check" in result.required_evidence


def test_backend_surface_pareto():
    """Backend surface triggers Pareto gate."""
    ctx = RunContext()
    result = ctx.get_scope_for_change(["backend"])
    assert result.gate_type == "pareto"


# ---------------------------------------------------------------------------
# get_scope_for_change - tool additions
# ---------------------------------------------------------------------------

def test_tool_addition_safety_gates():
    """Tool addition triggers safety and adversarial checks."""
    ctx = RunContext()
    result = ctx.get_scope_for_change(["tool_addition"])
    assert "adversarial test" in result.required_evidence
    assert "pass-rate" in result.required_evidence


# ---------------------------------------------------------------------------
# get_scope_for_change - edge cases
# ---------------------------------------------------------------------------

def test_empty_surfaces():
    """Empty surfaces list returns SYSTEM scope with strict gate."""
    ctx = RunContext()
    result = ctx.get_scope_for_change([])
    assert result.change_scope == ChangeScope.SYSTEM
    assert result.gate_type == "strict"
    assert "full regression suite" in result.required_evidence
    assert result.affected_surfaces == []


def test_combined_tools_and_backend():
    """Tools combined with backend get all relevant evidence."""
    ctx = RunContext()
    result = ctx.get_scope_for_change(["tools", "model_defaults"])
    assert "pass-rate" in result.required_evidence
    assert "safety review" in result.required_evidence
