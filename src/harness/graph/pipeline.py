"""Orchestrate the full 4-stage knowledge graph pipeline."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from harness.agent_backend import AgentBackend
from harness.graph.assembly import GraphAssembler
from harness.graph.extraction import GraphExtractor
from harness.graph.provenance import ProvenanceRecord, ProvenanceTracker
from harness.graph.query import GraphQuerier
from harness.graph.resolution import EntityResolver
from harness.graph.types import ExtractedGraph, ResolutionCluster

# Optional networkx import

try:
    import networkx as nx
except Exception:  # pragma: no cover
    nx = None  # type: ignore[misc]


class KnowledgeGraphPipeline:
    """Orchestrate extraction → resolution → assembly → query.

    This is the main entry-point for building and querying a knowledge
    graph from a corpus of documents.

    Example::

        backend = MockBackend()
        pipeline = KnowledgeGraphPipeline(backend)
        graph = pipeline.build(apollo_documents)
        result = pipeline.query("Who walked on the Moon?")
    """

    def __init__(
        self,
        backend: AgentBackend,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.backend = backend
        self.config = config or {}
        self.extractor = GraphExtractor(
            backend=backend,
            entity_types=self.config.get(
                "entity_types",
                ["PERSON", "ORGANIZATION", "LOCATION", "MISSION", "VEHICLE"],
            ),
            relation_types=self.config.get(
                "relation_types",
                [
                    "launched from", "landed on", "walked on", "orbited",
                    "designed", "built", "commanded", "directed", "worked for",
                ],
            ),
        )
        self.resolver = EntityResolver(
            similarity_threshold=self.config.get("similarity_threshold", 0.85),
        )
        self.assembler = GraphAssembler()
        self.tracker = ProvenanceTracker()
        self.graph: Optional[Any] = None
        self._clusters: List[ResolutionCluster] = []
        self._extracted: List[ExtractedGraph] = []

    def build(self, documents: List[str]) -> Any:
        """Run the full pipeline on a list of documents.

        Stages:
        1. **Extract** -- :meth:`GraphExtractor.extract_batch`
        2. **Resolve** -- :meth:`EntityResolver.resolve`
        3. **Assemble** -- :meth:`GraphAssembler.assemble`
        4. **Track** -- record provenance for all elements

        Args:
            documents: List of raw document texts.

        Returns:
            The assembled graph (``networkx.MultiDiGraph`` or fallback).
        """
        # Stage 1: Extract
        self._extracted = self.extractor.extract_batch(documents)

        # Stage 2: Resolve
        self._clusters = self.resolver.resolve(self._extracted)

        # Stage 3: Assemble
        all_relations = []
        for eg in self._extracted:
            all_relations.extend(eg.relations)
        self.graph = self.assembler.assemble(self._clusters, all_relations)

        # Stage 4: Provenance tracking
        self._record_provenance()

        return self.graph

    def query(self, question: str, **kwargs: Any) -> Dict[str, Any]:
        """Query the built graph.

        Args:
            question: Natural-language question.
            **kwargs: Forwarded to :meth:`GraphQuerier.query`.

        Returns:
            Structured answer dictionary.

        Raises:
            RuntimeError: If :meth:`build` has not been called yet.
        """
        if self.graph is None:
            raise RuntimeError("Graph not built. Call build() first.")
        querier = GraphQuerier(self.graph, self.backend)
        return querier.query(question, **kwargs)

    def get_provenance(self, element_id: str) -> Optional[ProvenanceRecord]:
        """Retrieve provenance for a graph element.

        Args:
            element_id: Node or edge identifier.

        Returns:
            :class:`ProvenanceRecord` or ``None``.
        """
        return self.tracker.get(element_id)

    def verify(self, element_id: str) -> bool:
        """Verify provenance for a graph element.

        Args:
            element_id: Node or edge identifier.

        Returns:
            ``True`` if the element has valid provenance.
        """
        return self.tracker.verify(element_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _record_provenance(self) -> None:
        """Record provenance for all canonical nodes and edges."""
        from datetime import datetime

        from datetime import timezone
        ts = datetime.now(timezone.utc).replace(tzinfo=None)
        for cluster in self._clusters:
            ent = cluster.canonical_entity
            self.tracker.record(
                ent.entity_id,
                ProvenanceRecord(
                    source_document=", ".join(
                        eg.source_document for eg in self._extracted if eg.entities
                    ),
                    extraction_timestamp=ts,
                    agent_id="GraphExtractor",
                    confidence=cluster.confidence,
                    extraction_method=cluster.resolution_method,
                    metadata={"member_count": len(cluster.members)},
                ),
            )

        # Edges provenance
        edge_idx = 0
        for eg in self._extracted:
            for rel in eg.relations:
                edge_id = f"edge_{edge_idx}_{rel.relation_id}"
                self.tracker.record(
                    edge_id,
                    ProvenanceRecord(
                        source_document=eg.source_document,
                        extraction_timestamp=ts,
                        agent_id="GraphExtractor",
                        confidence=rel.confidence,
                        extraction_method="pattern",
                        metadata={"predicate": rel.predicate},
                    ),
                )
                edge_idx += 1
