"""Swarm orchestrator for the Harness framework.

Provides dynamic agent spawning, work-stealing task distribution, and
consensus-based result aggregation.
"""

from harness.swarm.types import (
    ConsensusReport,
    SwarmResult,
    SwarmTask,
    SwarmWorker,
)
from harness.swarm.work_stealing import WorkStealingQueue
from harness.swarm.coordinator import SwarmCoordinator

__all__ = [
    "ConsensusReport",
    "SwarmResult",
    "SwarmTask",
    "SwarmWorker",
    "WorkStealingQueue",
    "SwarmCoordinator",
]
