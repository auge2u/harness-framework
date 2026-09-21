"""Semantic lifecycle hooks backed by the reflex (System 1) tier.

These hooks are **opt-in** variants of the regex-based gates in
:mod:`harness.lifecycle.builtin_hooks`.  Instead of brittle pattern
matching, each semantic hook asks a natural-language yes/no question via
:meth:`harness.reflex.primitives.ReflexPrimitives.bool_gate` and maps the
returned probability onto an :class:`~harness.lifecycle.types.ApprovalMode`
decision using configurable thresholds:

* ``p >= block_threshold``  -> :attr:`ApprovalMode.REJECT`
* ``p >= review_threshold`` -> :attr:`ApprovalMode.CONFIRM`
* otherwise                 -> :attr:`ApprovalMode.AUTO`

Semantic hooks are **additive**: registering them never removes or alters
the existing regex hooks, so both tiers can coexist in the same
:class:`~harness.lifecycle.hooks.LifecycleManager` chain.

All hooks fail closed by default: if the reflex backend raises, the hook
returns :attr:`ApprovalMode.CONFIRM` (never crashing the chain) unless
``fail_closed=False`` is requested, in which case it degrades to
:attr:`ApprovalMode.AUTO`.
"""

from __future__ import annotations

import time
from typing import Any, Dict, FrozenSet, List, Optional

from harness.lifecycle.types import (
    ApprovalMode,
    HookPoint,
    HookResult,
    ToolCallContext,
)
from harness.lifecycle.hooks import LifecycleHook, LifecycleManager
from harness.reflex.primitives import ReflexPrimitives

__all__ = [
    "SemanticHookBase",
    "SemanticDangerousCommandHook",
    "SemanticSecretLeakHook",
    "SemanticPatchRiskHook",
    "register_semantic_gates",
]

#: Registry of the available semantic gates, keyed by short name.
_SEMANTIC_HOOK_CLASSES: Dict[str, type] = {}


class SemanticHookBase(LifecycleHook):
    """Base class for reflex-backed semantic lifecycle hooks.

    Subclasses define :attr:`question_template` (optionally containing a
    ``{content}`` placeholder), :attr:`hook_point`, :attr:`priority`,
    :attr:`name`, and the set of tool names they engage on via
    :attr:`engaged_tool_names` (empty means "engage on every context").

    Args:
        primitives: The :class:`ReflexPrimitives` wrapper used to evaluate
            the yes/no gate question.
        block_threshold: Probability at or above which the hook returns
            :attr:`ApprovalMode.REJECT`.
        review_threshold: Probability at or above which the hook returns
            :attr:`ApprovalMode.CONFIRM` (when below *block_threshold*).
        fail_closed: When ``True`` (default), backend/primitive exceptions
            produce :attr:`ApprovalMode.CONFIRM`; when ``False`` they
            degrade to :attr:`ApprovalMode.AUTO`.  The hook never raises.
    """

    hook_point: HookPoint = HookPoint.PRE_TOOL_CALL
    priority: int = 100
    name: str = ""

    #: Natural-language yes/no question.  May contain a ``{content}``
    #: placeholder that is filled with the rendered context text.
    question_template: str = ""

    #: Tool names this hook engages on.  An empty set means the hook
    #: engages on every context passed to it.
    engaged_tool_names: FrozenSet[str] = frozenset()

    def __init__(
        self,
        primitives: ReflexPrimitives,
        block_threshold: float = 0.85,
        review_threshold: float = 0.5,
        fail_closed: bool = True,
    ) -> None:
        self.primitives = primitives
        self.block_threshold = float(block_threshold)
        self.review_threshold = float(review_threshold)
        self.fail_closed = bool(fail_closed)

    # ------------------------------------------------------------------
    # Engagement / rendering
    # ------------------------------------------------------------------

    def should_engage(self, context: Any) -> bool:
        """Return ``True`` when this hook should evaluate *context*.

        Non-:class:`ToolCallContext` values only engage when the hook is
        tool-agnostic (:attr:`engaged_tool_names` is empty).  Otherwise
        the hook engages when ``context.tool_name`` is in
        :attr:`engaged_tool_names`.
        """
        if not self.engaged_tool_names:
            return True
        if not isinstance(context, ToolCallContext):
            return False
        return context.tool_name in self.engaged_tool_names

    def render_context(self, context: Any) -> str:
        """Render *context* into the text evaluated by the reflex gate.

        For :class:`ToolCallContext` this concatenates the string values
        of ``tool_arguments``; other contexts fall back to ``str()``.
        Subclasses may override for more targeted extraction.
        """
        if isinstance(context, ToolCallContext):
            parts = [
                str(value)
                for value in context.tool_arguments.values()
                if isinstance(value, (str, int, float))
            ]
            return "\n".join(parts)
        if isinstance(context, dict):
            return "\n".join(str(value) for value in context.values())
        return str(context)

    def build_question(self, content: str) -> str:
        """Build the bool-gate question, filling ``{content}`` if present."""
        template = self.question_template
        if "{content}" in template:
            return template.format(content=content)
        return template

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, context: Any) -> HookResult:
        """Evaluate the semantic gate for *context*.

        Returns :attr:`ApprovalMode.AUTO` without touching the backend
        when the hook does not engage on this context.  Otherwise maps
        the reflex probability onto an approval decision and records
        ``probability``, ``question`` and ``latency_ms`` in the result
        metadata.  Backend failures follow the :attr:`fail_closed`
        policy and never propagate.
        """
        if not self.should_engage(context):
            return HookResult(
                decision=ApprovalMode.AUTO,
                message=f"Hook '{self.name}' does not engage on this context",
                metadata={"hook": self.name, "engaged": False},
            )

        content = self.render_context(context)
        question = self.build_question(content)
        start = time.monotonic()

        try:
            result = self.primitives.bool_gate(
                question,
                content,
                block_threshold=self.block_threshold,
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - start) * 1000.0
            decision = ApprovalMode.CONFIRM if self.fail_closed else ApprovalMode.AUTO
            return HookResult(
                decision=decision,
                message=(
                    f"Semantic gate '{self.name}' backend failed "
                    f"({type(exc).__name__}: {exc}) — "
                    f"{'failing closed' if self.fail_closed else 'failing open'}"
                ),
                metadata={
                    "hook": self.name,
                    "engaged": True,
                    "error": f"{type(exc).__name__}: {exc}",
                    "fail_closed": self.fail_closed,
                    "question": question,
                    "latency_ms": latency_ms,
                },
            )

        latency_ms = (time.monotonic() - start) * 1000.0
        probability = float(result.value)

        metadata: Dict[str, Any] = {
            "hook": self.name,
            "engaged": True,
            "probability": probability,
            "confidence": result.confidence,
            "question": question,
            "block_threshold": self.block_threshold,
            "review_threshold": self.review_threshold,
            "latency_ms": latency_ms,
            "reflex_latency_ms": result.latency_ms,
        }

        if probability >= self.block_threshold:
            return HookResult(
                decision=ApprovalMode.REJECT,
                message=(
                    f"Semantic gate '{self.name}' rejected: probability "
                    f"{probability:.2f} >= block threshold {self.block_threshold:.2f}"
                ),
                metadata=metadata,
            )
        if probability >= self.review_threshold:
            return HookResult(
                decision=ApprovalMode.CONFIRM,
                message=(
                    f"Semantic gate '{self.name}' requests review: probability "
                    f"{probability:.2f} >= review threshold {self.review_threshold:.2f}"
                ),
                metadata=metadata,
            )
        return HookResult(
            decision=ApprovalMode.AUTO,
            message=(
                f"Semantic gate '{self.name}' passed: probability "
                f"{probability:.2f} below review threshold {self.review_threshold:.2f}"
            ),
            metadata=metadata,
        )


class SemanticDangerousCommandHook(SemanticHookBase):
    """Semantic gate for destructive shell commands.

    Engages only on ``RunCommandTool`` contexts and asks the reflex
    backend whether the command is destructive, exfiltrating, or
    security-weakening.  Complements the regex-based
    :class:`~harness.lifecycle.builtin_hooks.DangerousCommandHook` — it
    does not replace it.
    """

    hook_point = HookPoint.PRE_TOOL_CALL
    priority = 10
    name = "semantic_dangerous_command"
    engaged_tool_names = frozenset({"RunCommandTool"})
    question_template = (
        "Does this shell command cause irreversible destruction, "
        "exfiltrate data, or weaken system security?"
    )

    def render_context(self, context: Any) -> str:
        """Extract the shell command string from the tool arguments."""
        if isinstance(context, ToolCallContext):
            return str(context.tool_arguments.get("command", ""))
        return super().render_context(context)


class SemanticSecretLeakHook(SemanticHookBase):
    """Semantic gate for credential / secret exfiltration.

    Engages on ``WriteFileTool`` and ``RunCommandTool`` contexts and asks
    the reflex backend whether the content exposes a live credential,
    private key, or secret value.
    """

    hook_point = HookPoint.PRE_TOOL_CALL
    priority = 15
    name = "semantic_secret_leak"
    engaged_tool_names = frozenset({"WriteFileTool", "RunCommandTool"})
    question_template = (
        "Does this content expose a live credential, private key, "
        "or secret value?"
    )


class SemanticPatchRiskHook(SemanticHookBase):
    """Semantic gate for risky patches.

    Runs at the :attr:`HookPoint.PRE_PATCH_APPLY` point and asks the
    reflex backend whether the change expands privileges, exfiltrates
    data, or disables safety checks.  Engages on any context (patches
    are not necessarily tool calls).
    """

    hook_point = HookPoint.PRE_PATCH_APPLY
    priority = 10
    name = "semantic_patch_risk"
    engaged_tool_names = frozenset()
    question_template = (
        "Does this change expand privileges, exfiltrate data, "
        "or disable safety checks?"
    )

    def render_context(self, context: Any) -> str:
        """Render patch contexts (ToolCallContext, dict, or raw text)."""
        if isinstance(context, dict):
            return "\n".join(str(value) for value in context.values())
        return super().render_context(context)


_SEMANTIC_HOOK_CLASSES.update(
    {
        "command": SemanticDangerousCommandHook,
        "secret": SemanticSecretLeakHook,
        "patch": SemanticPatchRiskHook,
    }
)


def register_semantic_gates(
    manager: LifecycleManager,
    primitives: ReflexPrimitives,
    hooks: Optional[List[str]] = None,
) -> List[str]:
    """Register semantic (reflex-backed) gates on *manager*.

    This is strictly additive: existing regex hooks registered on the
    manager are left untouched.

    Args:
        manager: The :class:`LifecycleManager` to register hooks on.
        primitives: The :class:`ReflexPrimitives` instance shared by all
            registered semantic hooks.
        hooks: Optional subset of ``{"command", "secret", "patch"}``
            selecting which gates to register.  ``None`` (the default)
            registers all three.

    Returns:
        The list of registered hook names, in registration order.

    Raises:
        ValueError: If *hooks* contains an unknown gate name.
    """
    selected = list(hooks) if hooks is not None else list(_SEMANTIC_HOOK_CLASSES)
    unknown = [name for name in selected if name not in _SEMANTIC_HOOK_CLASSES]
    if unknown:
        raise ValueError(
            f"Unknown semantic gate(s): {unknown}. "
            f"Valid gates: {sorted(_SEMANTIC_HOOK_CLASSES)}"
        )

    registered: List[str] = []
    for key in selected:
        hook = _SEMANTIC_HOOK_CLASSES[key](primitives)
        manager.register(hook)
        registered.append(hook.name)
    return registered
