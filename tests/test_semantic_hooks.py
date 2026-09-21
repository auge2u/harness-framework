"""Tests for the semantic (reflex-backed) lifecycle hooks."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

import pytest

from harness.lifecycle.types import (
    ApprovalMode,
    HookPoint,
    HookResult,
    ToolCallContext,
)
from harness.lifecycle.hooks import LifecycleManager
from harness.lifecycle.builtin_hooks import (
    DangerousCommandHook,
    FileWriteApprovalHook,
)
from harness.lifecycle.semantic_hooks import (
    SemanticDangerousCommandHook,
    SemanticHookBase,
    SemanticPatchRiskHook,
    SemanticSecretLeakHook,
    register_semantic_gates,
)
from harness.reflex.backend import MockReflexBackend
from harness.reflex.primitives import ReflexPrimitives, ReflexResult


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def backend() -> MockReflexBackend:
    """Deterministic mock reflex backend."""
    return MockReflexBackend()


@pytest.fixture
def primitives(backend: MockReflexBackend) -> ReflexPrimitives:
    """Reflex primitives over the mock backend."""
    return ReflexPrimitives(backend)


def make_context(
    tool_name: str = "RunCommandTool",
    args: Optional[Dict[str, Any]] = None,
) -> ToolCallContext:
    """Build a ToolCallContext for testing."""
    return ToolCallContext(
        tool_name=tool_name,
        tool_arguments=args or {"command": "ls -la"},
        agent_id="agent-1",
        scenario_id="scenario-1",
        execution_id="exec-1",
        timestamp=datetime.now(),
    )


def fake_bool_result(probability: float) -> ReflexResult:
    """Build a fake bool-gate ReflexResult with a fixed probability."""
    return ReflexResult(
        primitive="bool",
        value=probability,
        confidence=probability if probability >= 0.5 else 1.0 - probability,
        latency_ms=115.0,
        escalate=probability >= 0.85,
        detail={"question": "fake", "action": "BLOCK" if probability >= 0.85 else "PASS"},
    )


class RaisingBackend(MockReflexBackend):
    """Mock backend whose bool_check always raises."""

    def bool_check(self, question: str, context: str) -> float:
        raise RuntimeError("backend unavailable")


# ---------------------------------------------------------------------------
# SemanticDangerousCommandHook
# ---------------------------------------------------------------------------


class TestSemanticDangerousCommandHook:
    def test_hook_identity(self, primitives: ReflexPrimitives) -> None:
        hook = SemanticDangerousCommandHook(primitives)
        assert hook.hook_point is HookPoint.PRE_TOOL_CALL
        assert hook.priority == 10
        assert hook.name == "semantic_dangerous_command"

    def test_blocks_dangerous_command(
        self,
        primitives: ReflexPrimitives,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Teach the deterministic mock that 'rm -rf' is suspicious so the
        # real bool_check path returns 0.96 (>= block threshold).
        monkeypatch.setattr(
            MockReflexBackend,
            "SUSPICIOUS_KEYWORDS",
            MockReflexBackend.SUSPICIOUS_KEYWORDS + ("rm -rf",),
        )
        hook = SemanticDangerousCommandHook(primitives)
        result = hook.execute(make_context(args={"command": "rm -rf /"}))
        assert result.decision is ApprovalMode.REJECT
        assert result.metadata["probability"] == pytest.approx(0.96)
        assert "reject" in result.message.lower()

    def test_passes_safe_command(self, primitives: ReflexPrimitives) -> None:
        hook = SemanticDangerousCommandHook(primitives)
        result = hook.execute(make_context(args={"command": "ls -la"}))
        assert result.decision is ApprovalMode.AUTO
        assert result.metadata["probability"] == pytest.approx(0.02)

    def test_skips_non_command_tool_without_backend(
        self,
        primitives: ReflexPrimitives,
        backend: MockReflexBackend,
    ) -> None:
        hook = SemanticDangerousCommandHook(primitives)
        result = hook.execute(make_context(tool_name="ReadFileTool", args={"path": "/etc/hosts"}))
        assert result.decision is ApprovalMode.AUTO
        assert result.metadata["engaged"] is False
        assert backend.call_count == 0  # backend never invoked

    def test_review_band_returns_confirm(
        self,
        primitives: ReflexPrimitives,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            primitives,
            "bool_gate",
            lambda *a, **kw: fake_bool_result(0.6),
        )
        hook = SemanticDangerousCommandHook(primitives)
        result = hook.execute(make_context(args={"command": "chmod 777 /tmp/x"}))
        assert result.decision is ApprovalMode.CONFIRM
        assert result.metadata["probability"] == pytest.approx(0.6)

    def test_block_threshold_boundary(
        self,
        primitives: ReflexPrimitives,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            primitives,
            "bool_gate",
            lambda *a, **kw: fake_bool_result(0.85),
        )
        hook = SemanticDangerousCommandHook(primitives)
        result = hook.execute(make_context())
        assert result.decision is ApprovalMode.REJECT

    def test_fail_closed_on_backend_error(self) -> None:
        primitives = ReflexPrimitives(RaisingBackend())
        hook = SemanticDangerousCommandHook(primitives)  # fail_closed=True default
        result = hook.execute(make_context(args={"command": "ls"}))
        assert result.decision is ApprovalMode.CONFIRM
        assert "backend failed" in result.message.lower()
        assert "RuntimeError" in result.metadata["error"]
        assert result.metadata["fail_closed"] is True

    def test_fail_open_on_backend_error(self) -> None:
        primitives = ReflexPrimitives(RaisingBackend())
        hook = SemanticDangerousCommandHook(primitives, fail_closed=False)
        result = hook.execute(make_context(args={"command": "ls"}))
        assert result.decision is ApprovalMode.AUTO
        assert result.metadata["fail_closed"] is False

    def test_metadata_includes_latency_probability_question(
        self,
        primitives: ReflexPrimitives,
    ) -> None:
        hook = SemanticDangerousCommandHook(primitives)
        result = hook.execute(make_context(args={"command": "ls -la"}))
        metadata = result.metadata
        assert "latency_ms" in metadata
        assert metadata["latency_ms"] >= 0.0
        assert metadata["reflex_latency_ms"] == pytest.approx(115.0)
        assert metadata["probability"] == pytest.approx(0.02)
        assert "irreversible destruction" in metadata["question"]
        assert metadata["hook"] == "semantic_dangerous_command"

    def test_non_tool_context_does_not_engage(self, primitives: ReflexPrimitives) -> None:
        hook = SemanticDangerousCommandHook(primitives)
        result = hook.execute({"not": "a tool context"})
        assert result.decision is ApprovalMode.AUTO
        assert result.metadata["engaged"] is False


# ---------------------------------------------------------------------------
# SemanticSecretLeakHook
# ---------------------------------------------------------------------------


class TestSemanticSecretLeakHook:
    def test_hook_identity(self, primitives: ReflexPrimitives) -> None:
        hook = SemanticSecretLeakHook(primitives)
        assert hook.hook_point is HookPoint.PRE_TOOL_CALL
        assert hook.priority == 15
        assert hook.name == "semantic_secret_leak"

    def test_blocks_secret_in_write_file(self, primitives: ReflexPrimitives) -> None:
        hook = SemanticSecretLeakHook(primitives)
        context = make_context(
            tool_name="WriteFileTool",
            args={"path": "config.py", "content": 'api_key = "AKIA1234567890"'},
        )
        result = hook.execute(context)
        assert result.decision is ApprovalMode.REJECT
        assert result.metadata["probability"] == pytest.approx(0.96)
        assert "credential" in result.metadata["question"]

    def test_passes_benign_write(self, primitives: ReflexPrimitives) -> None:
        hook = SemanticSecretLeakHook(primitives)
        context = make_context(
            tool_name="WriteFileTool",
            args={"path": "hello.py", "content": "print('hello world')"},
        )
        result = hook.execute(context)
        assert result.decision is ApprovalMode.AUTO

    def test_engages_on_run_command_tool(self, primitives: ReflexPrimitives) -> None:
        hook = SemanticSecretLeakHook(primitives)
        context = make_context(
            tool_name="RunCommandTool",
            args={"command": "echo hunter2 > password.txt"},
        )
        result = hook.execute(context)
        assert result.decision is ApprovalMode.REJECT

    def test_skips_other_tools_without_backend(
        self,
        primitives: ReflexPrimitives,
        backend: MockReflexBackend,
    ) -> None:
        hook = SemanticSecretLeakHook(primitives)
        context = make_context(tool_name="ReadFileTool", args={"path": "config.py"})
        result = hook.execute(context)
        assert result.decision is ApprovalMode.AUTO
        assert backend.call_count == 0


# ---------------------------------------------------------------------------
# SemanticPatchRiskHook
# ---------------------------------------------------------------------------


class TestSemanticPatchRiskHook:
    def test_hook_point_is_pre_patch_apply(self, primitives: ReflexPrimitives) -> None:
        hook = SemanticPatchRiskHook(primitives)
        assert hook.hook_point is HookPoint.PRE_PATCH_APPLY
        assert hook.priority == 10
        assert hook.name == "semantic_patch_risk"

    def test_blocks_risky_patch(self, primitives: ReflexPrimitives) -> None:
        hook = SemanticPatchRiskHook(primitives)
        patch = {"diff": "+ db.query('DROP TABLE users'); password = open('.env').read()"}
        result = hook.execute(patch)
        assert result.decision is ApprovalMode.REJECT
        assert "disable safety checks" in result.metadata["question"]

    def test_passes_benign_patch(self, primitives: ReflexPrimitives) -> None:
        hook = SemanticPatchRiskHook(primitives)
        patch = {"diff": "+ def add(a, b):\n+     return a + b"}
        result = hook.execute(patch)
        assert result.decision is ApprovalMode.AUTO
        assert result.metadata["probability"] == pytest.approx(0.02)

    def test_accepts_string_context(self, primitives: ReflexPrimitives) -> None:
        hook = SemanticPatchRiskHook(primitives)
        result = hook.execute("refactor: rename variable x to total")
        assert result.decision is ApprovalMode.AUTO

    def test_accepts_tool_call_context(self, primitives: ReflexPrimitives) -> None:
        hook = SemanticPatchRiskHook(primitives)
        context = make_context(
            tool_name="ApplyPatchTool",
            args={"diff": "eval(user_input)"},
        )
        result = hook.execute(context)
        assert result.decision is ApprovalMode.REJECT


# ---------------------------------------------------------------------------
# register_semantic_gates
# ---------------------------------------------------------------------------


class TestRegisterSemanticGates:
    def test_registers_all_by_default(
        self,
        primitives: ReflexPrimitives,
    ) -> None:
        manager = LifecycleManager()
        names = register_semantic_gates(manager, primitives)
        assert names == [
            "semantic_dangerous_command",
            "semantic_secret_leak",
            "semantic_patch_risk",
        ]
        pre_tool_names = [h.name for h in manager.get_hooks(HookPoint.PRE_TOOL_CALL)]
        assert "semantic_dangerous_command" in pre_tool_names
        assert "semantic_secret_leak" in pre_tool_names
        pre_patch_names = [h.name for h in manager.get_hooks(HookPoint.PRE_PATCH_APPLY)]
        assert pre_patch_names == ["semantic_patch_risk"]

    def test_subset_respected(self, primitives: ReflexPrimitives) -> None:
        manager = LifecycleManager()
        names = register_semantic_gates(manager, primitives, hooks=["command", "patch"])
        assert names == ["semantic_dangerous_command", "semantic_patch_risk"]
        pre_tool_names = [h.name for h in manager.get_hooks(HookPoint.PRE_TOOL_CALL)]
        assert pre_tool_names == ["semantic_dangerous_command"]

    def test_unknown_gate_raises(self, primitives: ReflexPrimitives) -> None:
        manager = LifecycleManager()
        with pytest.raises(ValueError, match="Unknown semantic gate"):
            register_semantic_gates(manager, primitives, hooks=["command", "bogus"])

    def test_priority_order_vs_regex_hooks(self, primitives: ReflexPrimitives) -> None:
        manager = LifecycleManager()
        manager.register(FileWriteApprovalHook())  # priority 20
        manager.register(DangerousCommandHook())  # priority 10
        register_semantic_gates(manager, primitives)
        ordered = [
            (h.name, h.priority) for h in manager.get_hooks(HookPoint.PRE_TOOL_CALL)
        ]
        assert ordered == [
            ("dangerous_command_gate", 10),
            ("semantic_dangerous_command", 10),  # stable sort: registered later
            ("semantic_secret_leak", 15),
            ("file_write_approval", 20),
        ]

    def test_coexistence_leaves_regex_hooks_untouched(
        self,
        primitives: ReflexPrimitives,
    ) -> None:
        manager = LifecycleManager()
        regex_hook = DangerousCommandHook()
        manager.register(regex_hook)
        register_semantic_gates(manager, primitives)
        hooks = manager.get_hooks(HookPoint.PRE_TOOL_CALL)
        assert regex_hook in hooks
        # Regex hook still behaves exactly as before (tool_name 'run_command').
        result = regex_hook.execute(
            make_context(tool_name="run_command", args={"command": "rm -rf /"})
        )
        assert result.decision is ApprovalMode.REJECT


# ---------------------------------------------------------------------------
# Chain integration
# ---------------------------------------------------------------------------


class TestChainIntegration:
    def test_regex_rejects_first_and_aborts_chain(
        self,
        primitives: ReflexPrimitives,
        backend: MockReflexBackend,
    ) -> None:
        manager = LifecycleManager()
        manager.register(DangerousCommandHook())
        register_semantic_gates(manager, primitives, hooks=["command"])
        # Regex hook engages on 'run_command' and rejects before semantic runs.
        result = manager.execute(
            HookPoint.PRE_TOOL_CALL,
            make_context(tool_name="run_command", args={"command": "rm -rf /"}),
        )
        assert result.decision is ApprovalMode.REJECT
        assert result.metadata["aborted_by"] == "dangerous_command_gate"
        assert backend.call_count == 0  # semantic hook never reached

    def test_semantic_rejects_when_regex_passes(
        self,
        primitives: ReflexPrimitives,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            MockReflexBackend,
            "SUSPICIOUS_KEYWORDS",
            MockReflexBackend.SUSPICIOUS_KEYWORDS + ("rm -rf",),
        )
        manager = LifecycleManager()
        manager.register(DangerousCommandHook())
        register_semantic_gates(manager, primitives, hooks=["command"])
        # 'RunCommandTool' is not gated by the regex hook ('run_command' only),
        # so the chain reaches the semantic hook, which rejects.
        result = manager.execute(
            HookPoint.PRE_TOOL_CALL,
            make_context(tool_name="RunCommandTool", args={"command": "rm -rf /"}),
        )
        assert result.decision is ApprovalMode.REJECT
        assert result.metadata["aborted_by"] == "semantic_dangerous_command"
        assert result.metadata["probability"] == pytest.approx(0.96)

    def test_both_hooks_pass_clean_command(self, primitives: ReflexPrimitives) -> None:
        manager = LifecycleManager()
        manager.register(DangerousCommandHook())
        register_semantic_gates(manager, primitives, hooks=["command"])
        result = manager.execute(
            HookPoint.PRE_TOOL_CALL,
            make_context(tool_name="RunCommandTool", args={"command": "ls -la"}),
        )
        assert result.decision is ApprovalMode.AUTO

    def test_fail_closed_confirm_does_not_abort_chain(self) -> None:
        manager = LifecycleManager()
        manager.register(DangerousCommandHook())
        register_semantic_gates(manager, ReflexPrimitives(RaisingBackend()), hooks=["command"])
        result = manager.execute(
            HookPoint.PRE_TOOL_CALL,
            make_context(tool_name="RunCommandTool", args={"command": "ls"}),
        )
        # Chain is not aborted; final decision is the semantic hook's CONFIRM.
        assert result.decision is ApprovalMode.CONFIRM

    def test_audit_log_records_semantic_executions(
        self,
        primitives: ReflexPrimitives,
    ) -> None:
        manager = LifecycleManager()
        register_semantic_gates(manager, primitives, hooks=["command"])
        manager.execute(
            HookPoint.PRE_TOOL_CALL,
            make_context(tool_name="RunCommandTool", args={"command": "ls"}),
        )
        entries = manager.get_audit_log_for_hook("semantic_dangerous_command")
        assert len(entries) == 1
        assert entries[0]["decision"] == "AUTO"
        assert entries[0]["metadata"]["probability"] == pytest.approx(0.02)


class TestSemanticHookBaseContract:
    def test_is_lifecycle_hook(self, primitives: ReflexPrimitives) -> None:
        hook = SemanticDangerousCommandHook(primitives)
        assert isinstance(hook, SemanticHookBase)
        assert repr(hook).startswith("SemanticDangerousCommandHook(")

    def test_custom_thresholds(
        self,
        primitives: ReflexPrimitives,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            primitives,
            "bool_gate",
            lambda *a, **kw: fake_bool_result(0.4),
        )
        hook = SemanticDangerousCommandHook(
            primitives, block_threshold=0.9, review_threshold=0.3
        )
        result = hook.execute(make_context())
        assert result.decision is ApprovalMode.CONFIRM
        assert result.metadata["block_threshold"] == pytest.approx(0.9)
        assert result.metadata["review_threshold"] == pytest.approx(0.3)
