"""Shared types for the Harness framework."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Dict, List, Optional


class SurfaceType(Enum):
    """Classification of system surfaces that can be tested."""
    API = auto()
    UI = auto()
    DATABASE = auto()
    FILESYSTEM = auto()
    NETWORK = auto()
    MEMORY = auto()
    KNOWLEDGE_GRAPH = auto()
    REFLEX = auto()  # System 1 rubric artifacts (bool/score/choice criteria)


class ChangeScope(Enum):
    """Scope of impact for a code change."""
    ATOMIC = auto()      # Single function/endpoint
    COMPONENT = auto()   # Related group of surfaces
    SYSTEM = auto()      # Full cross-cutting change


class Verdict(Enum):
    """Possible outcomes of a verification check."""
    PASS = auto()
    FAIL = auto()
    PARTIAL = auto()
    SKIP = auto()


@dataclass
class Surface:
    """Represents a testable surface of the system under test."""

    name: str
    type: SurfaceType
    schema: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    risk_score: float = 0.0

    def __post_init__(self):
        """Normalize name and ensure risk_score is a float."""
        if not self.name or not isinstance(self.name, str):
            raise ValueError("Surface name must be a non-empty string")
        self.name = self.name.strip()
        self.risk_score = float(self.risk_score)
        if self.dependencies is None:
            self.dependencies = []
        if self.schema is None:
            self.schema = {}


@dataclass
class TraceRecord:
    """Immutable record of a single harness execution trace."""

    trace_id: str
    scenario_id: str
    harness_version: str
    timestamp: datetime
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    verdict: Verdict = Verdict.SKIP
    verifier_results: Dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Ensure mutable defaults are safe and numeric fields are floats."""
        if self.inputs is None:
            self.inputs = {}
        if self.outputs is None:
            self.outputs = {}
        if self.verifier_results is None:
            self.verifier_results = {}
        if self.metadata is None:
            self.metadata = {}
        self.latency_ms = float(self.latency_ms)
        self.cost_usd = float(self.cost_usd)


@dataclass
class FailureSignature:
    """Signature for clustering related failures together."""

    cluster_id: str
    pattern: str
    surfaces: List[str] = field(default_factory=list)
    verifier_type: str = ""
    frequency: int = 0
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None

    def __post_init__(self):
        """Ensure surfaces list is initialized and frequency is an int."""
        if self.surfaces is None:
            self.surfaces = []
        self.frequency = int(self.frequency)


@dataclass
class HarnessProposal:
    """Represents a proposed change to the harness configuration."""

    proposal_id: str
    parent_version: str
    changes: Dict[str, Any] = field(default_factory=dict)
    proposer_type: str = "self"  # "self" | "meta" | "manual"
    proposer_id: Optional[str] = None
    rationale: str = ""
    estimated_impact: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        """Ensure mutable defaults are safe and validate proposer_type."""
        if self.changes is None:
            self.changes = {}
        if self.estimated_impact is None:
            self.estimated_impact = {}
        valid_proposers = {"self", "meta", "manual"}
        if self.proposer_type not in valid_proposers:
            raise ValueError(
                f"proposer_type must be one of {valid_proposers}, "
                f"got {self.proposer_type!r}"
            )

    def to_patches(self, baseline_config: Optional[Dict] = None) -> List[Any]:
        """Convert this proposal into HarnessPatch objects.

        Requires :meth:`HarnessPatch.from_proposal` — imports lazily to
        avoid circular dependencies.

        Args:
            baseline_config: Optional baseline config for looking up
                previous values (currently unused, reserved for future
                enrichment).

        Returns:
            List of :class:`~harness.patch.HarnessPatch` objects.
        """
        from harness.patch import HarnessPatch
        return HarnessPatch.from_proposal(self)


@dataclass
class VerificationResult:
    """Result returned by a verifier after checking expected vs actual output."""

    verdict: Verdict
    score: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)
    feedback: str = ""

    def __post_init__(self):
        """Ensure details dict is initialized and score is a float."""
        if self.details is None:
            self.details = {}
        self.score = float(self.score)
