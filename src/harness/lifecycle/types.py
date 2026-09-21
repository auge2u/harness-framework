"""Core types for the Harness lifecycle hook system."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Dict, Optional


class ApprovalMode(Enum):
    """Level of autonomy for agent actions.

    - :attr:`AUTO` — Execute without confirmation.
    - :attr:`CONFIRM` — Pause for human approval.
    - :attr:`SIMULATE` — Show what would happen, do not execute.
    - :attr:`NEVER` — Block entirely.
    - :attr:`REJECT` — Reject and log.
    """

    AUTO = auto()
    CONFIRM = auto()
    SIMULATE = auto()
    NEVER = auto()
    REJECT = auto()


class HookPoint(Enum):
    """Lifecycle points where hooks can be registered."""

    PRE_TOOL_CALL = auto()
    POST_TOOL_CALL = auto()
    PRE_PROPOSAL = auto()
    POST_EVALUATION = auto()
    PRE_PATCH_APPLY = auto()
    POST_PATCH_APPLY = auto()
    PRE_SCENARIO_RUN = auto()
    POST_SCENARIO_RUN = auto()
    ON_ERROR = auto()
    ON_SHUTDOWN = auto()


@dataclass
class ToolCallContext:
    """Context for a tool call hook.

    Attributes
    ----------
    tool_name:
        The name of the tool being invoked.
    tool_arguments:
        The arguments passed to the tool.
    agent_id:
        Identifier of the agent making the call.
    scenario_id:
        Identifier of the current scenario.
    execution_id:
        Unique identifier for this execution.
    timestamp:
        When the tool call was initiated.
    surface:
        Optional surface classification for the call.
    estimated_cost_usd:
        Optional estimated cost of this call in USD.
    """

    tool_name: str
    tool_arguments: Dict[str, Any]
    agent_id: str
    scenario_id: str
    execution_id: str
    timestamp: datetime
    surface: str = ""
    estimated_cost_usd: float = 0.0

    def with_updated_args(self, **kwargs: Any) -> "ToolCallContext":
        """Return a new context with updated tool arguments."""
        new_args = dict(self.tool_arguments)
        new_args.update(kwargs)
        return ToolCallContext(
            tool_name=self.tool_name,
            tool_arguments=new_args,
            agent_id=self.agent_id,
            scenario_id=self.scenario_id,
            execution_id=self.execution_id,
            timestamp=self.timestamp,
            surface=self.surface,
            estimated_cost_usd=self.estimated_cost_usd,
        )


@dataclass
class HookResult:
    """Result from executing a hook.

    Attributes
    ----------
    decision:
        The approval decision from this hook.
    modified_context:
        If not ``None``, the updated context to pass to the next hook.
    message:
        Human-readable message explaining the decision.
    metadata:
        Arbitrary metadata about the hook execution.
    """

    decision: ApprovalMode
    modified_context: Optional[ToolCallContext] = None
    message: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
