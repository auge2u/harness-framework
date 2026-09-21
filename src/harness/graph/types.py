"""Core graph data types for the Harness knowledge graph pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Entity:
    """A canonical or surface-form entity extracted from a document.

    Attributes:
        entity_id: Unique identifier for this entity.
        name: Primary display name.
        type: Entity type -- e.g. ``"PERSON"``, ``"ORGANIZATION"``, ``"PRODUCT"``.
        description: One-sentence disambiguation.
        aliases: Alternative names / surface forms.
        metadata: Arbitrary additional metadata.
    """

    entity_id: str
    name: str
    type: str
    description: str = ""
    aliases: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.aliases is None:
            self.aliases = []
        if self.metadata is None:
            self.metadata = {}


@dataclass
class Relation:
    """A directed relationship between two entities.

    Attributes:
        relation_id: Unique identifier for this relation.
        source: Source entity_id or name.
        predicate: Short verb phrase describing the relationship.
        target: Target entity_id or name.
        confidence: Confidence score in ``[0.0, 1.0]``.
        provenance: Arbitrary provenance metadata.
    """

    relation_id: str
    source: str
    predicate: str
    target: str
    confidence: float = 1.0
    provenance: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.provenance is None:
            self.provenance = {}
        self.confidence = float(self.confidence)


@dataclass
class ExtractedGraph:
    """Entities and relations extracted from a single document.

    Attributes:
        entities: List of extracted entities.
        relations: List of extracted relations.
        source_document: Identifier for the source document.
        extraction_metadata: Arbitrary metadata about the extraction run.
    """

    entities: List[Entity] = field(default_factory=list)
    relations: List[Relation] = field(default_factory=list)
    source_document: str = ""
    extraction_metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.entities is None:
            self.entities = []
        if self.relations is None:
            self.relations = []
        if self.extraction_metadata is None:
            self.extraction_metadata = {}


@dataclass
class ResolutionCluster:
    """A cluster of surface-form entities resolved to a single canonical entity.

    Attributes:
        canonical_entity: The canonical entity representing this cluster.
        members: Surface-form entities that resolved to this canonical entity.
        confidence: Overall confidence of the resolution.
        resolution_method: Method used -- ``"exact"``, ``"description_similarity"``,
            ``"substring"``, or ``"manual"``.
    """

    canonical_entity: Entity
    members: List[Entity] = field(default_factory=list)
    confidence: float = 1.0
    resolution_method: str = "exact"

    def __post_init__(self):
        if self.members is None:
            self.members = []
        self.confidence = float(self.confidence)
