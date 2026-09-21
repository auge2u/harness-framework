"""Knowledge graph pipeline for the Harness framework.

Follows the Anthropic Graph-Engineering Playbook:
    extraction → resolution → assembly → querying

All components are designed to work with :class:`~harness.agent_backend.MockBackend`
for deterministic testing without requiring a live LLM.
"""

from harness.graph.assembly import GraphAssembler
from harness.graph.extraction import GraphExtractor
from harness.graph.pipeline import KnowledgeGraphPipeline
from harness.graph.provenance import ProvenanceRecord, ProvenanceTracker
from harness.graph.query import GraphQuerier
from harness.graph.resolution import EntityResolver
from harness.graph.types import (
    Entity,
    ExtractedGraph,
    Relation,
    ResolutionCluster,
)

__all__ = [
    "Entity",
    "ExtractedGraph",
    "Relation",
    "ResolutionCluster",
    "GraphExtractor",
    "EntityResolver",
    "GraphAssembler",
    "GraphQuerier",
    "ProvenanceRecord",
    "ProvenanceTracker",
    "KnowledgeGraphPipeline",
]
