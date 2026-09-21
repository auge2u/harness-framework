"""Lifecycle hooks and approval modes for the Harness framework.

This package provides:
- :mod:`types` — Core types (ApprovalMode, HookPoint, ToolCallContext, HookResult)
- :mod:`hooks` — LifecycleHook base class and LifecycleManager
- :mod:`builtin_hooks` — Built-in hooks (dangerous command gate, file write approval, audit logger, cost budget)
- :mod:`approval` — ApprovalManager for per-tool/per-surface approval modes
- :mod:`integration` — Integration with ToolRegistry via instrument_tools()
"""

from __future__ import annotations

from harness.lifecycle.types import (
    ApprovalMode,
    HookPoint,
    ToolCallContext,
    HookResult,
)
from harness.lifecycle.hooks import LifecycleHook, LifecycleManager
from harness.lifecycle.builtin_hooks import (
    DangerousCommandHook,
    FileWriteApprovalHook,
    AuditLogHook,
    CostBudgetHook,
)
from harness.lifecycle.approval import ApprovalManager
from harness.lifecycle.integration import (
    instrument_tools,
    InstrumentedTool,
)
from harness.lifecycle.semantic_hooks import (
    SemanticHookBase,
    SemanticDangerousCommandHook,
    SemanticSecretLeakHook,
    SemanticPatchRiskHook,
    register_semantic_gates,
)

__all__ = [
    "ApprovalMode",
    "HookPoint",
    "ToolCallContext",
    "HookResult",
    "LifecycleHook",
    "LifecycleManager",
    "DangerousCommandHook",
    "FileWriteApprovalHook",
    "AuditLogHook",
    "CostBudgetHook",
    "ApprovalManager",
    "instrument_tools",
    "InstrumentedTool",
    "SemanticHookBase",
    "SemanticDangerousCommandHook",
    "SemanticSecretLeakHook",
    "SemanticPatchRiskHook",
    "register_semantic_gates",
]
