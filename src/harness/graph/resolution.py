"""Stage 2: Entity resolution -- surface forms into canonical nodes."""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from harness.graph.types import Entity, ExtractedGraph, ResolutionCluster


class EntityResolver:
    """Resolves surface-form entities into canonical nodes.

    Clusters entities by type and merges similar names using exact,
    case-insensitive, substring, and token-overlap similarity signals.
    """

    def __init__(self, similarity_threshold: float = 0.85):
        self.similarity_threshold = similarity_threshold
        self.alias_map: Dict[str, str] = {}  # surface_form -> canonical_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, graphs: List[ExtractedGraph]) -> List[ResolutionCluster]:
        """Cluster entities by type, then merge similar ones.

        Resolution logic:
        1. Gather all entities from all graphs.
        2. Group entities by ``type``.
        3. Within each type, greedily cluster by name similarity.
        4. Create canonical entities for each cluster.
        5. Build ``alias_map`` mapping all surface forms to canonical IDs.

        Args:
            graphs: List of extracted graphs whose entities should be resolved.

        Returns:
            List of :class:`ResolutionCluster` objects.
        """
        all_entities: List[Entity] = []
        for graph in graphs:
            all_entities.extend(graph.entities)

        # Group by type
        by_type: Dict[str, List[Entity]] = {}
        for ent in all_entities:
            by_type.setdefault(ent.type, []).append(ent)

        clusters: List[ResolutionCluster] = []
        for etype, entities in by_type.items():
            clusters.extend(self._cluster_entities(entities))

        # Build alias map
        self.alias_map = {}
        for cluster in clusters:
            canonical_id = cluster.canonical_entity.entity_id
            for member in cluster.members:
                self.alias_map[member.name.lower()] = canonical_id
                for alias in member.aliases:
                    self.alias_map[alias.lower()] = canonical_id
            # Also map canonical name
            self.alias_map[cluster.canonical_entity.name.lower()] = canonical_id

        return clusters

    def resolve_entity(self, entity: Entity) -> str:
        """Resolve a single entity to its canonical ID.

        Args:
            entity: Entity to resolve.

        Returns:
            Canonical entity ID, or the entity's own ID if no match.
        """
        key = entity.name.lower()
        if key in self.alias_map:
            return self.alias_map[key]
        # Try aliases
        for alias in entity.aliases:
            akey = alias.lower()
            if akey in self.alias_map:
                return self.alias_map[akey]
        return entity.entity_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cluster_entities(self, entities: List[Entity]) -> List[ResolutionCluster]:
        """Cluster a list of same-type entities."""
        if not entities:
            return []

        clusters: List[List[Entity]] = []
        used: Set[int] = set()

        for i, ent_i in enumerate(entities):
            if i in used:
                continue
            cluster = [ent_i]
            used.add(i)
            for j, ent_j in enumerate(entities):
                if j in used or i == j:
                    continue
                sim = self._name_similarity(ent_i.name, ent_j.name)
                if sim >= self.similarity_threshold:
                    cluster.append(ent_j)
                    used.add(j)
            clusters.append(cluster)

        return [self._make_cluster(c) for c in clusters]

    def _make_cluster(self, members: List[Entity]) -> ResolutionCluster:
        """Create a ResolutionCluster from a list of member entities."""
        # Pick canonical: longest name, or first if tie
        canonical = max(members, key=lambda e: len(e.name))
        # Compute aggregate confidence based on resolution method
        methods: Set[str] = set()
        for m in members:
            for other in members:
                if m is other:
                    continue
                sim = self._name_similarity(m.name, other.name)
                if sim == 1.0:
                    methods.add("exact")
                elif sim >= 0.95:
                    methods.add("case_insensitive")
                elif sim >= 0.9:
                    methods.add("substring")
                else:
                    methods.add("description_similarity")

        resolution_method = "exact" if "exact" in methods else (
            "case_insensitive" if "case_insensitive" in methods else (
                "substring" if "substring" in methods else "description_similarity"
            )
        )

        # Collect all aliases
        all_aliases: Set[str] = set()
        for m in members:
            all_aliases.update(m.aliases)
            all_aliases.add(m.name)
        all_aliases.discard(canonical.name)

        canonical_entity = Entity(
            entity_id=f"canon_{canonical.name.lower().replace(' ', '_')}",
            name=canonical.name,
            type=canonical.type,
            description=canonical.description,
            aliases=sorted(all_aliases),
            metadata={"member_count": len(members)},
        )

        return ResolutionCluster(
            canonical_entity=canonical_entity,
            members=members,
            confidence=1.0 if resolution_method == "exact" else 0.9,
            resolution_method=resolution_method,
        )

    def _name_similarity(self, name1: str, name2: str) -> float:
        """Compute name similarity using multiple signals.

        Signals (in order of precedence):
        * Exact match = 1.0
        * Case-insensitive match = 0.95
        * One contains the other (after stripping) = 0.9
        * Jaccard similarity on word tokens = 0.0--1.0

        Args:
            name1: First name.
            name2: Second name.

        Returns:
            Similarity score in ``[0.0, 1.0]``.
        """
        n1 = name1.strip()
        n2 = name2.strip()
        if n1 == n2:
            return 1.0
        if n1.lower() == n2.lower():
            return 0.95
        if n1.lower() in n2.lower() or n2.lower() in n1.lower():
            return 0.9
        # Jaccard on tokens
        tokens1 = set(n1.lower().split())
        tokens2 = set(n2.lower().split())
        if not tokens1 and not tokens2:
            return 1.0
        if not tokens1 or not tokens2:
            return 0.0
        intersection = tokens1 & tokens2
        union = tokens1 | tokens2
        return len(intersection) / len(union)
