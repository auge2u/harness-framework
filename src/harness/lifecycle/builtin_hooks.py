"""Built-in lifecycle hooks for the Harness framework."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from harness.lifecycle.types import ApprovalMode, HookPoint, ToolCallContext, HookResult
from harness.lifecycle.hooks import LifecycleHook


class DangerousCommandHook(LifecycleHook):
    """Hook that gates dangerous shell commands.

    Matches command strings against a set of dangerous regex patterns.
    Returns :attr:`ApprovalMode.REJECT` if any pattern matches,
    :attr:`ApprovalMode.AUTO` otherwise.
    """

    DANGEROUS_PATTERNS: List[re.Pattern] = [
        re.compile(r"rm\s+-rf", re.IGNORECASE),
        re.compile(r">\s*/dev/", re.IGNORECASE),
        re.compile(r"\bsudo\b", re.IGNORECASE),
        re.compile(r"chmod\s+777", re.IGNORECASE),
        re.compile(r"curl\s+.*\|\s*sh", re.IGNORECASE),
        re.compile(r"wget\s+.*\|\s*sh", re.IGNORECASE),
    ]

    def __init__(self) -> None:
        self.hook_point = HookPoint.PRE_TOOL_CALL
        self.priority = 10
        self.name = "dangerous_command_gate"

    def execute(self, context: Any) -> HookResult:
        if not isinstance(context, ToolCallContext):
            return HookResult(
                decision=ApprovalMode.AUTO,
                message="Non-tool context — skipping dangerous command check",
            )

        # Only gate run_command tool calls
        if context.tool_name != "run_command":
            return HookResult(
                decision=ApprovalMode.AUTO,
                message=f"Tool '{context.tool_name}' is not a command executor",
            )

        command = context.tool_arguments.get("command", "")
        if not command:
            return HookResult(
                decision=ApprovalMode.AUTO,
                message="Empty command — nothing to check",
            )

        for pattern in self.DANGEROUS_PATTERNS:
            match = pattern.search(command)
            if match:
                return HookResult(
                    decision=ApprovalMode.REJECT,
                    message=(
                        f"Dangerous command pattern detected: "
                        f"'{match.group(0)}' in command '{command}'"
                    ),
                    metadata={
                        "matched_pattern": pattern.pattern,
                        "matched_text": match.group(0),
                        "command": command,
                    },
                )

        return HookResult(
            decision=ApprovalMode.AUTO,
            message=f"Command '{command}' passed safety check",
        )


class FileWriteApprovalHook(LifecycleHook):
    """Hook that requires confirmation for file writes.

    Returns :attr:`ApprovalMode.CONFIRM` for ``write_file`` tool calls
    unless the target path matches one of the ``auto_approve_paths``.
    """

    def __init__(self, auto_approve_paths: Optional[List[str]] = None) -> None:
        self.hook_point = HookPoint.PRE_TOOL_CALL
        self.priority = 20
        self.name = "file_write_approval"
        self.auto_approve_paths: List[str] = auto_approve_paths or []

    def execute(self, context: Any) -> HookResult:
        if not isinstance(context, ToolCallContext):
            return HookResult(
                decision=ApprovalMode.AUTO,
                message="Non-tool context — skipping file write check",
            )

        if context.tool_name != "write_file":
            return HookResult(
                decision=ApprovalMode.AUTO,
                message=f"Tool '{context.tool_name}' is not a file writer",
            )

        path = context.tool_arguments.get("path", "")

        # Check auto-approve paths
        for approved in self.auto_approve_paths:
            if path.startswith(approved) or approved in path:
                return HookResult(
                    decision=ApprovalMode.AUTO,
                    message=f"Path '{path}' matches auto-approve pattern '{approved}'",
                )

        return HookResult(
            decision=ApprovalMode.CONFIRM,
            message=f"File write to '{path}' requires confirmation",
            metadata={"path": path},
        )


class AuditLogHook(LifecycleHook):
    """Hook that logs all tool calls to the audit trail.

    Optionally persists records to a :class:`TraceStore` if provided.
    """

    def __init__(self, trace_store: Optional[Any] = None) -> None:
        self.hook_point = HookPoint.POST_TOOL_CALL
        self.priority = 100
        self.name = "audit_logger"
        self.trace_store = trace_store

    def execute(self, context: Any) -> HookResult:
        if not isinstance(context, ToolCallContext):
            return HookResult(
                decision=ApprovalMode.AUTO,
                message="Non-tool context — skipping audit log",
            )

        audit_record: Dict[str, Any] = {
            "tool_name": context.tool_name,
            "tool_arguments": context.tool_arguments,
            "agent_id": context.agent_id,
            "scenario_id": context.scenario_id,
            "execution_id": context.execution_id,
            "timestamp": context.timestamp.isoformat(),
        }

        # Persist to trace store if available
        if self.trace_store is not None:
            try:
                from harness.core.types import TraceRecord, Verdict

                trace = TraceRecord(
                    trace_id=f"audit-{context.execution_id}",
                    scenario_id=context.scenario_id,
                    harness_version="lifecycle",
                    timestamp=context.timestamp,
                    inputs={
                        "tool_name": context.tool_name,
                        "tool_arguments": context.tool_arguments,
                    },
                    outputs={"audit": True},
                    cost_usd=context.estimated_cost_usd,
                    latency_ms=0.0,
                    verdict=Verdict.PASS,
                    metadata={
                        "agent_id": context.agent_id,
                        "execution_id": context.execution_id,
                        "surface": context.surface,
                    },
                )
                self.trace_store.record(trace)
                audit_record["trace_store_persisted"] = True
            except Exception as exc:
                audit_record["trace_store_error"] = str(exc)

        return HookResult(
            decision=ApprovalMode.AUTO,
            message=f"Audited tool call: {context.tool_name}",
            metadata={"audit_record": audit_record},
        )


class CostBudgetHook(LifecycleHook):
    """Hook that enforces cost budgets per scenario.

    Maintains a running total of estimated costs per scenario and
    returns :attr:`ApprovalMode.NEVER` if the budget would be exceeded.
    """

    def __init__(self, budget_usd: float = 10.0) -> None:
        self.hook_point = HookPoint.PRE_TOOL_CALL
        self.priority = 5  # High priority — check before execution
        self.name = "cost_budget"
        self.budget: float = budget_usd
        self._spent: Dict[str, float] = {}
        self._lock = __import__("threading").Lock()

    def execute(self, context: Any) -> HookResult:
        if not isinstance(context, ToolCallContext):
            return HookResult(
                decision=ApprovalMode.AUTO,
                message="Non-tool context — skipping budget check",
            )

        scenario_id = context.scenario_id
        estimated_cost = context.estimated_cost_usd

        with self._lock:
            current_spent = self._spent.get(scenario_id, 0.0)
            projected = current_spent + estimated_cost

            if projected > self.budget:
                remaining = self.budget - current_spent
                return HookResult(
                    decision=ApprovalMode.NEVER,
                    message=(
                        f"Cost budget exceeded for scenario '{scenario_id}': "
                        f"projected ${projected:.4f} > budget ${self.budget:.4f} "
                        f"(remaining: ${remaining:.4f})"
                    ),
                    metadata={
                        "scenario_id": scenario_id,
                        "budget_usd": self.budget,
                        "spent_usd": current_spent,
                        "estimated_cost_usd": estimated_cost,
                        "projected_usd": projected,
                    },
                )

            # Approve and accumulate cost
            self._spent[scenario_id] = projected

        return HookResult(
            decision=ApprovalMode.AUTO,
            message=(
                f"Cost budget check passed for scenario '{scenario_id}': "
                f"spent ${projected:.4f} / ${self.budget:.4f}"
            ),
            metadata={
                "scenario_id": scenario_id,
                "budget_usd": self.budget,
                "spent_usd": projected,
            },
        )

    def get_spent(self, scenario_id: str) -> float:
        """Get the current spent amount for a scenario."""
        with self._lock:
            return self._spent.get(scenario_id, 0.0)

    def reset_spent(self, scenario_id: Optional[str] = None) -> None:
        """Reset spent tracking.

        Args:
            scenario_id: If provided, reset only that scenario.  Otherwise
                reset all scenarios.
        """
        with self._lock:
            if scenario_id is not None:
                self._spent.pop(scenario_id, None)
            else:
                self._spent.clear()
