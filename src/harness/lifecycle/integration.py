"""Integration between lifecycle hooks and the Harness tool registry."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from harness.tools import Tool, ToolRegistry, ToolSchema
from harness.lifecycle.types import ApprovalMode, HookPoint, ToolCallContext, HookResult
from harness.lifecycle.hooks import LifecycleManager


class InstrumentedTool(Tool):
    """Wrapper that adds lifecycle hooks around a :class:`Tool`.

    Every call triggers:

    1. ``PRE_TOOL_CALL`` hooks.
    2. Original tool execution (if approved).
    3. ``POST_TOOL_CALL`` hooks.

    The wrapper preserves the original tool's :attr:`schema` and delegates
    all attribute access not explicitly handled to the wrapped tool.
    """

    def __init__(self, wrapped: Tool, lifecycle: LifecycleManager) -> None:
        self._wrapped = wrapped
        self._lifecycle = lifecycle
        # Copy schema reference so callers can inspect it directly
        self.schema = wrapped.schema

    @property
    def wrapped(self) -> Tool:
        """The underlying tool instance."""
        return self._wrapped

    def get_schema(self) -> ToolSchema:
        """Return this tool's schema (delegated to wrapped tool)."""
        return self._wrapped.get_schema()

    def execute(
        self,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute the tool with lifecycle hooks.

        Args:
            params: Tool parameters.
            context: Optional execution context.

        Returns:
            The tool's execution result, possibly wrapped with hook metadata.
        """
        ctx = context or {}

        # Build ToolCallContext
        tool_context = ToolCallContext(
            tool_name=self.schema.name,
            tool_arguments=dict(params),
            agent_id=ctx.get("agent_id", "anonymous"),
            scenario_id=ctx.get("scenario_id", "default"),
            execution_id=ctx.get("execution_id") or str(uuid.uuid4()),
            timestamp=datetime.now(),
            surface=ctx.get("surface", ""),
            estimated_cost_usd=ctx.get("estimated_cost_usd", 0.0),
        )

        # Phase 1: PRE_TOOL_CALL hooks
        pre_result = self._lifecycle.execute(HookPoint.PRE_TOOL_CALL, tool_context)

        # Abort if REJECT or NEVER
        if pre_result.decision in (ApprovalMode.REJECT, ApprovalMode.NEVER):
            return {
                "status": "denied",
                "error": pre_result.message or f"Blocked by {pre_result.decision.name}",
                "hook_decision": pre_result.decision.name,
                "tool_name": self.schema.name,
                "execution_id": tool_context.execution_id,
            }

        # Use modified context if provided
        if pre_result.modified_context is not None:
            tool_context = pre_result.modified_context

        # Phase 2: Execute wrapped tool (unless SIMULATE)
        if pre_result.decision == ApprovalMode.SIMULATE:
            tool_result: Dict[str, Any] = {
                "status": "simulated",
                "message": "Tool execution simulated — not actually run",
                "tool_name": self.schema.name,
                "params": params,
                "execution_id": tool_context.execution_id,
            }
        else:
            # Pass through to wrapped tool with updated context
            exec_ctx = dict(ctx)
            exec_ctx["execution_id"] = tool_context.execution_id
            exec_ctx["lifecycle_approved"] = True
            tool_result = self._wrapped.execute(tool_context.tool_arguments, exec_ctx)

        # Phase 3: POST_TOOL_CALL hooks
        post_context = ToolCallContext(
            tool_name=tool_context.tool_name,
            tool_arguments=tool_context.tool_arguments,
            agent_id=tool_context.agent_id,
            scenario_id=tool_context.scenario_id,
            execution_id=tool_context.execution_id,
            timestamp=datetime.now(),
            surface=tool_context.surface,
            estimated_cost_usd=tool_context.estimated_cost_usd,
        )
        # Attach tool result to context for post hooks
        post_context_metadata = {"tool_result": tool_result}

        post_result = self._lifecycle.execute(HookPoint.POST_TOOL_CALL, post_context)

        # Merge post-hook decision into result if it changed
        if post_result.decision in (ApprovalMode.REJECT, ApprovalMode.NEVER):
            tool_result["post_hook_blocked"] = True
            tool_result["post_hook_message"] = post_result.message

        return tool_result

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access to the wrapped tool."""
        return getattr(self._wrapped, name)

    def __repr__(self) -> str:
        return f"InstrumentedTool(wrapped={self._wrapped!r})"


def instrument_tools(
    tool_registry: ToolRegistry,
    lifecycle_manager: LifecycleManager,
) -> ToolRegistry:
    """Wrap all tools in a registry with lifecycle hooks.

    Returns a **new** :class:`ToolRegistry` where every tool is wrapped
    in an :class:`InstrumentedTool`.  The original registry is not
    modified.

    Args:
        tool_registry: The source registry.
        lifecycle_manager: The lifecycle manager to attach.

    Returns:
        A new registry with instrumented tools.
    """
    new_registry = ToolRegistry()
    for name in tool_registry.list_tools():
        original = tool_registry.get(name)
        instrumented = InstrumentedTool(original, lifecycle_manager)
        new_registry.register(name, instrumented, override=False)
    return new_registry
