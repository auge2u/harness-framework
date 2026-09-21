"""Plugin integration for the knowledge graph surface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from harness.agent_backend import AgentBackend, MockBackend
from harness.core.types import Surface, SurfaceType
from harness.graph.pipeline import KnowledgeGraphPipeline
from harness.plugins import Plugin, PluginCapabilities, PluginContext, TrustLevel


@dataclass
class KnowledgeGraphPlugin(Plugin):
    """Plugin for knowledge graph surface management.

    Integrates the 4-stage knowledge graph pipeline (extraction →
    resolution → assembly → query) as a first-class Harness surface.
    """

    name: str = "knowledge_graph"
    version: str = "0.1.0"
    surfaces: List[str] = field(default_factory=lambda: ["knowledge_graph"])
    trust_level: TrustLevel = field(default=TrustLevel.BUILTIN)
    capabilities: PluginCapabilities = field(
        default_factory=lambda: PluginCapabilities(
            can_read=["knowledge_graph"],
            can_write=["knowledge_graph"],
            can_execute=True,
            can_access_filesystem=True,
        )
    )
    dependencies: List[str] = field(default_factory=list)
    enabled: bool = True

    # Plugin-specific configuration
    backend: Optional[AgentBackend] = None
    entity_types: List[str] = field(
        default_factory=lambda: [
            "PERSON", "ORGANIZATION", "LOCATION", "MISSION", "VEHICLE",
        ]
    )
    relation_types: List[str] = field(
        default_factory=lambda: [
            "launched from", "landed on", "walked on", "orbited",
            "designed", "built", "commanded", "directed", "worked for",
        ]
    )
    similarity_threshold: float = 0.85

    def __post_init__(self):
        if self.backend is None:
            self.backend = MockBackend()
        if self.entity_types is None:
            self.entity_types = []
        if self.relation_types is None:
            self.relation_types = []

    @property
    def surface_name(self) -> str:
        """Return the canonical surface name."""
        return "knowledge_graph"

    def apply(self, harness_config: Any, context: PluginContext) -> Dict[str, Any]:
        """Apply knowledge graph operations.

        Recognised operations (passed via ``context.metadata["operation"]``):

        * ``"build"`` -- build a graph from ``documents`` in metadata.
        * ``"query"`` -- query the built graph with ``question`` in metadata.

        Args:
            harness_config: Current harness configuration.
            context: Execution context with operation metadata.

        Returns:
            Result dictionary with ``status`` and operation-specific data.
        """
        operation = context.metadata.get("operation", "build")

        if operation == "build":
            documents = context.metadata.get("documents", [])
            pipeline = self._make_pipeline()
            graph = pipeline.build(documents)
            return {
                "status": "success",
                "operation": "build",
                "node_count": graph.number_of_nodes(),
                "edge_count": graph.number_of_edges(),
            }

        if operation == "query":
            documents = context.metadata.get("documents", [])
            question = context.metadata.get("question", "")
            pipeline = self._make_pipeline()
            pipeline.build(documents)
            result = pipeline.query(question)
            return {
                "status": "success",
                "operation": "query",
                "answer": result.get("answer", ""),
                "subgraph": result.get("subgraph", ""),
                "citations": result.get("citations", []),
            }

        return {
            "status": "failure",
            "error": f"Unknown operation: {operation}",
        }

    def validate(self, harness_config: Any) -> List[str]:
        """Validate graph configuration.

        Args:
            harness_config: Current harness configuration.

        Returns:
            List of validation error messages. Empty list means valid.
        """
        errors: List[str] = []
        if not self.backend:
            errors.append("KnowledgeGraphPlugin requires a backend.")
        if self.similarity_threshold < 0.0 or self.similarity_threshold > 1.0:
            errors.append("similarity_threshold must be in [0.0, 1.0].")
        if not self.entity_types:
            errors.append("At least one entity type must be configured.")
        if not self.relation_types:
            errors.append("At least one relation type must be configured.")
        return errors

    def get_surface(self) -> Surface:
        """Return the Surface descriptor for the knowledge graph surface."""
        return Surface(
            name=self.surface_name,
            type=SurfaceType.KNOWLEDGE_GRAPH,
            schema={
                "entity_types": self.entity_types,
                "relation_types": self.relation_types,
                "similarity_threshold": self.similarity_threshold,
            },
            dependencies=[],
            risk_score=0.2,
        )

    def _make_pipeline(self) -> KnowledgeGraphPipeline:
        """Create a configured pipeline instance."""
        return KnowledgeGraphPipeline(
            backend=self.backend,
            config={
                "entity_types": self.entity_types,
                "relation_types": self.relation_types,
                "similarity_threshold": self.similarity_threshold,
            },
        )
