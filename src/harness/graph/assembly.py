"""Stage 3: Assemble resolved entities into a queryable graph."""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional

from harness.graph.types import Relation, ResolutionCluster

# NetworkX is an optional dependency.  If unavailable we fall back to a
# minimal pure-Python directed graph implementation.

try:
    import networkx as nx

    HAS_NETWORKX = True
except Exception:  # pragma: no cover
    HAS_NETWORKX = False

    class _FallbackMultiDiGraph:  # type: ignore[no-redef]
        """Minimal fallback directed graph when NetworkX is unavailable."""

        def __init__(self) -> None:
            self._nodes: Dict[str, Dict[str, Any]] = {}
            self._edges: List[Dict[str, Any]] = []
            self._adj_out: Dict[str, List[int]] = {}
            self._adj_in: Dict[str, List[int]] = {}
            self._node_view = self._NodeView(self)

        @property
        def nodes(self):
            """Return a dict-like node view supporting ``G.nodes[n]``."""
            return self._node_view

        def add_node(self, node_for_adding: str, **attr: Any) -> None:
            """Add a node with optional attributes."""
            if node_for_adding not in self._nodes:
                self._nodes[node_for_adding] = dict(attr)
                self._adj_out[node_for_adding] = []
                self._adj_in[node_for_adding] = []
            else:
                self._nodes[node_for_adding].update(attr)

        def add_edge(self, u_of_edge: str, v_of_edge: str, **attr: Any) -> None:
            """Add a directed edge from *u_of_edge* to *v_of_edge*."""
            for n in (u_of_edge, v_of_edge):
                if n not in self._nodes:
                    self.add_node(n)
            edge_idx = len(self._edges)
            self._edges.append({"source": u_of_edge, "target": v_of_edge, **attr})
            self._adj_out[u_of_edge].append(edge_idx)
            self._adj_in[v_of_edge].append(edge_idx)

        class _NodeView:
            """Dict-like node view supporting ``view[node_id]`` access."""

            def __init__(self, graph: "_FallbackMultiDiGraph") -> None:
                self._graph = graph

            def __getitem__(self, n: str) -> Dict[str, Any]:
                return self._graph._nodes[n]

            def __call__(self, data: bool = False):
                if data:
                    return iter(self._graph._nodes.items())
                return iter(self._graph._nodes.keys())

            def __iter__(self):
                return iter(self._graph._nodes.keys())

        def nodes(self, data: bool = False):
            """Return node iterator."""
            if data:
                return iter(self._nodes.items())
            return iter(self._nodes.keys())

        def edges(self, data: bool = False):
            """Return edge iterator."""
            if data:
                return iter(
                    (e["source"], e["target"], {k: v for k, v in e.items() if k not in ("source", "target")})
                    for e in self._edges
                )
            return iter((e["source"], e["target"]) for e in self._edges)

        def has_node(self, n: str) -> bool:
            """Check if node *n* is in the graph."""
            return n in self._nodes

        def out_edges(self, nbunch: str, data: bool = False):
            """Return outgoing edges for *nbunch*."""
            idxs = self._adj_out.get(nbunch, [])
            if data:
                return iter(
                    (self._edges[i]["source"], self._edges[i]["target"],
                     {k: v for k, v in self._edges[i].items() if k not in ("source", "target")})
                    for i in idxs
                )
            return iter((self._edges[i]["source"], self._edges[i]["target"]) for i in idxs)

        def in_edges(self, nbunch: str, data: bool = False):
            """Return incoming edges for *nbunch*."""
            idxs = self._adj_in.get(nbunch, [])
            if data:
                return iter(
                    (self._edges[i]["source"], self._edges[i]["target"],
                     {k: v for k, v in self._edges[i].items() if k not in ("source", "target")})
                    for i in idxs
                )
            return iter((self._edges[i]["source"], self._edges[i]["target"]) for i in idxs)

        def number_of_nodes(self) -> int:
            """Return the number of nodes."""
            return len(self._nodes)

        def number_of_edges(self) -> int:
            """Return the number of edges."""
            return len(self._edges)

        def __getitem__(self, n: str) -> Dict[str, Any]:
            """Return node attribute dictionary."""
            return self._nodes[n]


if not HAS_NETWORKX:
    nx = type(sys)("networkx")  # type: ignore[misc]
    nx.MultiDiGraph = _FallbackMultiDiGraph  # type: ignore[misc]


class GraphAssembler:
    """Assemble resolved entities and relations into a directed multi-graph.

    Uses ``networkx.MultiDiGraph`` when available, otherwise a minimal
    pure-Python fallback so the pipeline works without optional dependencies.
    """

    def __init__(self) -> None:
        self.graph: Any = nx.MultiDiGraph()

    def assemble(
        self,
        clusters: List[ResolutionCluster],
        relations: List[Relation],
    ) -> Any:
        """Build graph from resolved clusters and relations.

        * Adds canonical entities as nodes with full metadata.
        * Adds relations as edges with predicate labels and provenance.

        Args:
            clusters: Resolved entity clusters.
            relations: All extracted relations (surface-form).

        Returns:
            A ``networkx.MultiDiGraph`` (or fallback equivalent).
        """
        # Build name -> canonical_id mapping
        name_to_canon: Dict[str, str] = {}
        for cluster in clusters:
            canon_id = cluster.canonical_entity.entity_id
            name_to_canon[cluster.canonical_entity.name.lower()] = canon_id
            for member in cluster.members:
                name_to_canon[member.name.lower()] = canon_id
                for alias in member.aliases:
                    name_to_canon[alias.lower()] = canon_id

        # Add nodes
        for cluster in clusters:
            ent = cluster.canonical_entity
            self.graph.add_node(
                ent.entity_id,
                name=ent.name,
                type=ent.type,
                description=ent.description,
                aliases=ent.aliases,
                metadata=ent.metadata,
                resolution_method=cluster.resolution_method,
                resolution_confidence=cluster.confidence,
            )

        # Add edges
        for rel in relations:
            source_canon = name_to_canon.get(rel.source.lower(), rel.source.lower().replace(" ", "_"))
            target_canon = name_to_canon.get(rel.target.lower(), rel.target.lower().replace(" ", "_"))
            # Ensure nodes exist
            for nid in (source_canon, target_canon):
                if not self.graph.has_node(nid):
                    self.graph.add_node(
                        nid,
                        name=nid,
                        type="UNKNOWN",
                        description="",
                        aliases=[],
                        metadata={},
                    )
            self.graph.add_edge(
                source_canon,
                target_canon,
                predicate=rel.predicate,
                confidence=rel.confidence,
                provenance=rel.provenance,
                relation_id=rel.relation_id,
            )

        return self.graph

    def get_node(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Get node data by canonical ID.

        Args:
            entity_id: Canonical node identifier.

        Returns:
            Node attribute dictionary, or ``None`` if not found.
        """
        if self.graph.has_node(entity_id):
            data = self.graph.nodes[entity_id]
            return dict(data) if data is not None else {}
        return None

    def get_edges(
        self,
        source: Optional[str] = None,
        target: Optional[str] = None,
        predicate: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query edges with optional filtering.

        Args:
            source: Filter by source node ID.
            target: Filter by target node ID.
            predicate: Filter by predicate label.

        Returns:
            List of edge dictionaries with keys ``source``, ``target``,
            ``predicate``, ``confidence``, and ``provenance``.
        """
        results: List[Dict[str, Any]] = []
        for u, v, data in self.graph.edges(data=True):  # type: ignore[union-attr]
            if source is not None and u != source:
                continue
            if target is not None and v != target:
                continue
            if predicate is not None and data.get("predicate") != predicate:
                continue
            results.append({
                "source": u,
                "target": v,
                "predicate": data.get("predicate", ""),
                "confidence": data.get("confidence", 1.0),
                "provenance": data.get("provenance", {}),
                "relation_id": data.get("relation_id", ""),
            })
        return results
