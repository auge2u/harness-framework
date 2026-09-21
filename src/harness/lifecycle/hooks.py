"""Lifecycle hook base class and manager."""

from __future__ import annotations

import threading
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

from harness.lifecycle.types import ApprovalMode, HookPoint, ToolCallContext, HookResult


class LifecycleHook(ABC):
    """Base class for lifecycle hooks.

    Hooks follow the chain-of-responsibility pattern:

    - Each hook can inspect the context.
    - Each hook can modify the context (via ``modified_context`` in the result).
    - Each hook can abort the chain by returning :attr:`ApprovalMode.REJECT`
      or :attr:`ApprovalMode.NEVER`.
    - Hooks execute in priority order (lower number = earlier).

    Attributes
    ----------
    hook_point:
        The :class:`HookPoint` at which this hook is executed.
    priority:
        Execution priority — lower numbers run first.  Default is ``100``.
    name:
        Unique identifier for this hook instance.  Used for unregistration
        and audit logging.
    """

    hook_point: HookPoint = HookPoint.PRE_TOOL_CALL
    priority: int = 100
    name: str = ""

    @abstractmethod
    def execute(self, context: Any) -> HookResult:
        """Execute the hook.

        Args:
            context: The execution context — typically a
                :class:`ToolCallContext` for tool-related hooks.

        Returns:
            A :class:`HookResult` with the decision, optional modified
            context, and a message.
        """
        ...

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"name={self.name!r}, "
            f"hook_point={self.hook_point.name}, "
            f"priority={self.priority})"
        )


class LifecycleManager:
    """Manages registration and execution of lifecycle hooks.

    Thread-safe via :class:`threading.RLock`.  Maintains an in-memory
    audit log of every hook execution.
    """

    def __init__(self) -> None:
        self._hooks: Dict[HookPoint, List[LifecycleHook]] = {
            hp: [] for hp in HookPoint
        }
        self._lock = threading.RLock()
        self._audit_log: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, hook: LifecycleHook) -> None:
        """Register a hook for its hook point.

        Args:
            hook: The hook to register.  Must have ``hook_point`` set.

        Raises:
            ValueError: If ``hook.hook_point`` is not a valid :class:`HookPoint`.
            ValueError: If ``hook.name`` is empty.
        """
        if not hook.name:
            raise ValueError("Hook must have a non-empty name")
        if not isinstance(hook.hook_point, HookPoint):
            raise ValueError(
                f"Invalid hook_point: {hook.hook_point!r}. "
                f"Must be a HookPoint enum member."
            )

        with self._lock:
            # Prevent duplicate names at the same hook point
            existing = [h.name for h in self._hooks[hook.hook_point]]
            if hook.name in existing:
                raise ValueError(
                    f"Hook '{hook.name}' is already registered at {hook.hook_point.name}"
                )
            self._hooks[hook.hook_point].append(hook)
            self._hooks[hook.hook_point].sort(key=lambda h: h.priority)

    def unregister(self, hook_name: str) -> bool:
        """Unregister a hook by name across all hook points.

        Args:
            hook_name: The unique name of the hook to remove.

        Returns:
            ``True`` if a hook was removed, ``False`` otherwise.
        """
        with self._lock:
            for hp, hooks in self._hooks.items():
                for i, h in enumerate(hooks):
                    if h.name == hook_name:
                        hooks.pop(i)
                        return True
            return False

    def get_hooks(self, hook_point: HookPoint) -> List[LifecycleHook]:
        """Get all registered hooks for a hook point, sorted by priority.

        Args:
            hook_point: The lifecycle point to query.

        Returns:
            A shallow copy of the hook list.
        """
        with self._lock:
            return list(self._hooks[hook_point])

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, hook_point: HookPoint, context: Any) -> HookResult:
        """Execute all hooks for a hook point.

        Algorithm:

        1. Retrieve hooks for *hook_point*, sorted by priority.
        2. For each hook, call ``execute(context)``.
        3. If ``result.decision`` is :attr:`ApprovalMode.REJECT` or
           :attr:`ApprovalMode.NEVER`, stop the chain and return immediately.
        4. If ``result.modified_context`` is not ``None``, update *context*
           for the next hook.
        5. If all hooks pass with :attr:`AUTO`, :attr:`CONFIRM`, or
           :attr:`SIMULATE`, return the final result.
        6. Record every hook execution in the audit log.

        Args:
            hook_point: The lifecycle point to execute.
            context: The initial context to pass to hooks.

        Returns:
            The final :class:`HookResult` from the chain.
        """
        hooks = self.get_hooks(hook_point)

        current_context = context
        final_result: Optional[HookResult] = None
        execution_id = str(uuid.uuid4())

        for hook in hooks:
            try:
                result = hook.execute(current_context)
            except Exception as exc:
                result = HookResult(
                    decision=ApprovalMode.REJECT,
                    message=f"Hook '{hook.name}' raised {type(exc).__name__}: {exc}",
                    metadata={"exception": str(exc)},
                )

            # Record audit entry
            audit_entry = self._make_audit_entry(
                execution_id=execution_id,
                hook_point=hook_point,
                hook=hook,
                context=current_context,
                result=result,
            )
            with self._lock:
                self._audit_log.append(audit_entry)

            # Update context if modified
            if result.modified_context is not None:
                current_context = result.modified_context

            final_result = result

            # Abort chain on REJECT or NEVER
            if result.decision in (ApprovalMode.REJECT, ApprovalMode.NEVER):
                return HookResult(
                    decision=result.decision,
                    modified_context=current_context,
                    message=result.message or f"Chain aborted by hook '{hook.name}'",
                    metadata={
                        "aborted_by": hook.name,
                        "hook_point": hook_point.name,
                        **result.metadata,
                    },
                )

        # Return final result or AUTO if no hooks
        if final_result is not None:
            return HookResult(
                decision=final_result.decision,
                modified_context=current_context,
                message=final_result.message,
                metadata=final_result.metadata,
            )

        return HookResult(
            decision=ApprovalMode.AUTO,
            modified_context=current_context,
            message="No hooks registered — auto-approved",
        )

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Get the audit log of all hook executions.

        Returns:
            A shallow copy of the audit log list.
        """
        with self._lock:
            return list(self._audit_log)

    def clear_audit_log(self) -> None:
        """Clear the audit log."""
        with self._lock:
            self._audit_log.clear()

    def get_audit_log_for_hook(self, hook_name: str) -> List[Dict[str, Any]]:
        """Get audit entries for a specific hook name.

        Args:
            hook_name: The hook name to filter by.

        Returns:
            Matching audit entries.
        """
        with self._lock:
            return [entry for entry in self._audit_log if entry.get("hook_name") == hook_name]

    def get_audit_log_for_hook_point(self, hook_point: HookPoint) -> List[Dict[str, Any]]:
        """Get audit entries for a specific hook point.

        Args:
            hook_point: The hook point to filter by.

        Returns:
            Matching audit entries.
        """
        hp_name = hook_point.name
        with self._lock:
            return [entry for entry in self._audit_log if entry.get("hook_point") == hp_name]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_audit_entry(
        execution_id: str,
        hook_point: HookPoint,
        hook: LifecycleHook,
        context: Any,
        result: HookResult,
    ) -> Dict[str, Any]:
        """Build an audit log entry for a single hook execution."""
        entry: Dict[str, Any] = {
            "execution_id": execution_id,
            "timestamp": datetime.now().isoformat(),
            "hook_point": hook_point.name,
            "hook_name": hook.name,
            "hook_class": hook.__class__.__name__,
            "priority": hook.priority,
            "decision": result.decision.name,
            "message": result.message,
        }

        # Include context summary if it's a ToolCallContext
        if isinstance(context, ToolCallContext):
            entry["context_summary"] = {
                "tool_name": context.tool_name,
                "agent_id": context.agent_id,
                "scenario_id": context.scenario_id,
                "execution_id": context.execution_id,
            }

        if result.metadata:
            entry["metadata"] = dict(result.metadata)

        return entry
