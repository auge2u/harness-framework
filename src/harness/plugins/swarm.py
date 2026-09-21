"""Swarm orchestration plugin for the Harness framework."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from harness.plugins import OrchestrationPlugin, PluginCapabilities, PluginContext
from harness.swarm.coordinator import SwarmCoordinator
from harness.swarm.types import SwarmTask


@dataclass
class SwarmOrchestrationPlugin(OrchestrationPlugin):
    """Orchestration plugin that uses swarm coordination.

    This plugin enables dynamic parallelisation of scenario execution by
    decomposing work into subtasks, distributing them across a pool of
    worker agents, and aggregating results with consensus detection.

    Attributes:
        max_workers: Maximum worker agents to spawn.
        consensus_threshold: Agreement level required for early termination.
        cost_budget_usd: Total cost budget for swarm execution.
        timeout_seconds: Wall-clock timeout for swarm runs.
    """

    name: str = "swarm_orchestration"
    surfaces: List[str] = field(default_factory=lambda: ["orchestration"])
    capabilities: PluginCapabilities = field(
        default_factory=lambda: PluginCapabilities(
            can_read=["orchestration"],
            can_write=["orchestration"],
            can_execute=True,
        )
    )
    max_workers: int = 10
    consensus_threshold: float = 0.8
    cost_budget_usd: float = 5.0
    timeout_seconds: float = 120.0

    @property
    def surface_name(self) -> str:
        """Return the surface name targeted by this plugin."""
        return "swarm_orchestration"

    def apply(self, harness_config: Any, context: PluginContext) -> Dict[str, Any]:
        """Apply swarm orchestration to a scenario or task.

        The *context.metadata* dict may contain a ``"task"`` key with a
        :class:`SwarmTask` description.  If absent, a default analysis task
        is created from the harness configuration.

        Args:
            harness_config: The current harness configuration object.
            context: Execution context for this plugin run.

        Returns:
            A dict with ``status``, ``report`` (the :class:`ConsensusReport`
            as a dict), and ``metrics``.
        """
        task_data = context.metadata.get("task")
        if task_data:
            if isinstance(task_data, SwarmTask):
                task = task_data
            else:
                task = SwarmTask(
                    task_id=task_data.get("task_id", "plugin-task"),
                    description=task_data.get("description", "Plugin task"),
                    task_type=task_data.get("task_type", "analyze"),
                    input_data=task_data.get("input_data", {}),
                )
        else:
            task = SwarmTask(
                task_id=f"plugin-{context.run_id}",
                description=f"Analyze harness config: {getattr(harness_config, 'name', 'unknown')}",
                task_type="analyze",
                input_data={"config": str(harness_config)},
            )

        coordinator = SwarmCoordinator(
            max_workers=self.max_workers,
            consensus_threshold=self.consensus_threshold,
            cost_budget_usd=self.cost_budget_usd,
            timeout_seconds=self.timeout_seconds,
        )

        report = coordinator.run(task)

        return {
            "status": "success",
            "report": {
                "agreement_score": report.agreement_score,
                "consensus_output": report.consensus_output,
                "confidence": report.confidence,
                "num_results": len(report.results),
                "num_dissenting": len(report.dissenting_views),
            },
            "metrics": {
                "workers_spawned": len(coordinator.workers),
                "cost_used_usd": coordinator.cost_used,
            },
        }

    def validate(self, harness_config: Any) -> List[str]:
        """Validate swarm configuration.

        Checks that plugin parameters are within acceptable bounds.

        Args:
            harness_config: The current harness configuration object.

        Returns:
            List of validation error messages.  An empty list means valid.
        """
        errors: List[str] = []
        if self.max_workers < 1:
            errors.append("max_workers must be >= 1")
        if self.max_workers > 100:
            errors.append("max_workers cannot exceed 100")
        if not (0.0 <= self.consensus_threshold <= 1.0):
            errors.append("consensus_threshold must be in [0, 1]")
        if self.cost_budget_usd < 0:
            errors.append("cost_budget_usd must be >= 0")
        if self.timeout_seconds < 1:
            errors.append("timeout_seconds must be >= 1")
        return errors
