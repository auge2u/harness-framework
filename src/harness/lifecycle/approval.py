"""Approval mode integration for the Harness framework."""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

from harness.lifecycle.types import ApprovalMode, ToolCallContext, HookResult


class ApprovalManager:
    """Manages approval modes per surface and per tool.

    Provides a resolution hierarchy:

    1. Per-tool mode (if set).
    2. Per-surface mode (if set).
    3. Default mode.

    Thread-safe via :class:`threading.RLock`.
    """

    def __init__(self, default_mode: ApprovalMode = ApprovalMode.AUTO) -> None:
        self._default_mode: ApprovalMode = default_mode
        self._per_surface_modes: Dict[str, ApprovalMode] = {}
        self._per_tool_modes: Dict[str, ApprovalMode] = {}
        self._lock = threading.RLock()
        self._approval_callback: Optional[Callable[[ToolCallContext, ApprovalMode], bool]] = None

    # ------------------------------------------------------------------
    # Mode configuration
    # ------------------------------------------------------------------

    def set_surface_mode(self, surface: str, mode: ApprovalMode) -> None:
        """Set approval mode for a surface.

        Args:
            surface: The surface identifier (e.g., ``"sandbox"``, ``"tools"``).
            mode: The approval mode to apply.
        """
        with self._lock:
            self._per_surface_modes[surface] = mode

    def set_tool_mode(self, tool: str, mode: ApprovalMode) -> None:
        """Set approval mode for a tool.

        Args:
            tool: The tool name (e.g., ``"write_file"``, ``"run_command"``).
            mode: The approval mode to apply.
        """
        with self._lock:
            self._per_tool_modes[tool] = mode

    def get_mode(self, tool_name: str, surface: str = "") -> ApprovalMode:
        """Get effective approval mode for a tool/surface combination.

        Resolution order:

        1. Per-tool mode if set.
        2. Per-surface mode if set.
        3. Default mode.

        Args:
            tool_name: The name of the tool being invoked.
            surface: The surface classification (optional).

        Returns:
            The resolved :class:`ApprovalMode`.
        """
        with self._lock:
            if tool_name in self._per_tool_modes:
                return self._per_tool_modes[tool_name]
            if surface in self._per_surface_modes:
                return self._per_surface_modes[surface]
            return self._default_mode

    def clear_surface_mode(self, surface: str) -> None:
        """Remove a per-surface mode override."""
        with self._lock:
            self._per_surface_modes.pop(surface, None)

    def clear_tool_mode(self, tool: str) -> None:
        """Remove a per-tool mode override."""
        with self._lock:
            self._per_tool_modes.pop(tool, None)

    def set_approval_callback(
        self, callback: Optional[Callable[[ToolCallContext, ApprovalMode], bool]]
    ) -> None:
        """Set a callback for interactive approval requests.

        The callback receives the :class:`ToolCallContext` and the
        :class:`ApprovalMode`, and must return ``True`` to approve or
        ``False`` to deny.
        """
        self._approval_callback = callback

    # ------------------------------------------------------------------
    # Approval request
    # ------------------------------------------------------------------

    def request_approval(self, context: ToolCallContext, mode: ApprovalMode) -> bool:
        """Request approval for an action.

        Behavior by mode:

        - :attr:`AUTO` — Return ``True`` immediately.
        - :attr:`SIMULATE` — Return ``True`` but mark as simulated.
        - :attr:`CONFIRM` — If an approval callback is set, delegate to it.
          Otherwise, return ``False`` (default-safe).
        - :attr:`NEVER` — Return ``False``.
        - :attr:`REJECT` — Return ``False``.

        Args:
            context: The tool call context.
            mode: The approval mode to apply.

        Returns:
            ``True`` if the action is approved, ``False`` otherwise.
        """
        if mode == ApprovalMode.AUTO:
            return True
        if mode == ApprovalMode.SIMULATE:
            return True
        if mode == ApprovalMode.CONFIRM:
            if self._approval_callback is not None:
                return self._approval_callback(context, mode)
            # Default-safe: if no callback, deny
            return False
        if mode in (ApprovalMode.NEVER, ApprovalMode.REJECT):
            return False

        # Unknown mode — default to safe
        return False

    def resolve_and_request(self, context: ToolCallContext) -> HookResult:
        """Resolve the approval mode for a context and request approval.

        This is a convenience method that combines :meth:`get_mode` and
        :meth:`request_approval` into a single call, returning a
        :class:`HookResult`.

        Args:
            context: The tool call context.

        Returns:
            A :class:`HookResult` with the decision and approval outcome.
        """
        mode = self.get_mode(context.tool_name, context.surface)
        approved = self.request_approval(context, mode)

        if mode == ApprovalMode.AUTO:
            return HookResult(
                decision=ApprovalMode.AUTO,
                message="Auto-approved",
            )
        if mode == ApprovalMode.SIMULATE:
            return HookResult(
                decision=ApprovalMode.SIMULATE,
                message="Simulated execution — would proceed",
            )
        if mode == ApprovalMode.CONFIRM:
            if approved:
                return HookResult(
                    decision=ApprovalMode.CONFIRM,
                    message="Confirmed by approval callback",
                )
            return HookResult(
                decision=ApprovalMode.REJECT,
                message="Confirmation denied by approval callback",
            )
        if mode == ApprovalMode.NEVER:
            return HookResult(
                decision=ApprovalMode.NEVER,
                message="Blocked by NEVER policy",
            )
        if mode == ApprovalMode.REJECT:
            return HookResult(
                decision=ApprovalMode.REJECT,
                message="Rejected by policy",
            )

        return HookResult(
            decision=ApprovalMode.AUTO,
            message="Fallback auto-approved",
        )
