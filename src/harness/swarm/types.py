"""Core data types for the Swarm orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from harness.agent_backend import AgentBackend


@dataclass
class SwarmTask:
    """A task that can be delegated to a worker agent.

    Attributes:
        task_id: Unique identifier for this task.
        description: Human-readable description of what the task does.
        task_type: Category of task -- ``"extract"``, ``"analyze"``,
            ``"synthesize"``, or ``"verify"``.
        input_data: Arbitrary input data for the task.
        priority: Priority level from 1 (highest) to 10 (lowest).
        estimated_tokens: Rough estimate of tokens this task will consume.
        dependencies: List of ``task_id`` values that must complete before
            this task can start.
    """

    task_id: str
    description: str
    task_type: str  # "extract" | "analyze" | "synthesize" | "verify"
    input_data: Dict[str, Any]
    priority: int = 5  # 1-10, lower = higher priority
    estimated_tokens: int = 1000
    dependencies: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.input_data is None:
            self.input_data = {}
        if self.dependencies is None:
            self.dependencies = []
        self.priority = max(1, min(10, int(self.priority)))
        self.estimated_tokens = max(0, int(self.estimated_tokens))


@dataclass
class SwarmWorker:
    """A worker agent in the swarm.

    Attributes:
        worker_id: Unique identifier for this worker.
        role: Worker role -- ``"coder"``, ``"explorer"``, ``"planner"``,
            ``"synthesizer"``, or ``"verifier"``.
        backend: The :class:`~harness.agent_backend.AgentBackend` that
            executes this worker's tasks.
        capabilities: List of capability strings this worker advertises.
        tasks_completed: Number of tasks successfully completed.
        tasks_failed: Number of tasks that failed.
        total_cost: Cumulative cost in USD for all tasks run by this worker.
    """

    worker_id: str
    role: str  # "coder" | "explorer" | "planner" | "synthesizer" | "verifier"
    backend: AgentBackend
    capabilities: List[str] = field(default_factory=list)
    tasks_completed: int = 0
    tasks_failed: int = 0
    total_cost: float = 0.0

    def __post_init__(self):
        if self.capabilities is None:
            self.capabilities = []


@dataclass
class SwarmResult:
    """Result from a single worker task execution.

    Attributes:
        task_id: The ``task_id`` of the task that was executed.
        worker_id: The ``worker_id`` of the worker that executed it.
        status: Execution status -- ``"completed"``, ``"failed"``, or
            ``"timeout"``.
        output: Arbitrary output data produced by the worker.
        cost: Cost in USD for this task execution.
        latency_ms: Wall-clock time in milliseconds.
        confidence: Worker's self-reported confidence in the result (0-1).
    """

    task_id: str
    worker_id: str
    status: str  # "completed" | "failed" | "timeout"
    output: Dict[str, Any] = field(default_factory=dict)
    cost: float = 0.0
    latency_ms: float = 0.0
    confidence: float = 0.0

    def __post_init__(self):
        if self.output is None:
            self.output = {}
        self.cost = float(self.cost)
        self.latency_ms = float(self.latency_ms)
        self.confidence = max(0.0, min(1.0, float(self.confidence)))


@dataclass
class ConsensusReport:
    """Aggregated results with consensus analysis.

    Attributes:
        results: All :class:`SwarmResult` objects collected.
        agreement_score: How much workers agree (0-1).
        consensus_output: The agreed-upon output dict.
        dissenting_views: Results that disagreed with the consensus.
        confidence: Overall confidence in the consensus (0-1).
    """

    results: List[SwarmResult]
    agreement_score: float
    consensus_output: Dict[str, Any]
    dissenting_views: List[SwarmResult]
    confidence: float

    def __post_init__(self):
        if self.results is None:
            self.results = []
        if self.consensus_output is None:
            self.consensus_output = {}
        if self.dissenting_views is None:
            self.dissenting_views = []
        self.agreement_score = float(self.agreement_score)
        self.confidence = float(self.confidence)
