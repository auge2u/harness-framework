"""Provenance tracking for knowledge graph elements."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class ProvenanceRecord:
    """Provenance metadata for a single graph element.

    Attributes:
        source_document: Identifier of the document this element came from.
        extraction_timestamp: When the element was extracted.
        agent_id: Identifier of the agent / extractor that produced this element.
        confidence: Confidence score in ``[0.0, 1.0]``.
        extraction_method: Method used -- e.g. ``"pattern"``, ``"llm"``, ``"manual"``.
        metadata: Arbitrary additional provenance metadata.
    """

    source_document: str = ""
    extraction_timestamp: datetime = field(default_factory=lambda: datetime.now().replace(microsecond=0))
    agent_id: str = ""
    confidence: float = 1.0
    extraction_method: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        self.confidence = float(self.confidence)


class ProvenanceTracker:
    """Track provenance for all graph elements.

    Maintains a mapping from element identifiers to their
    :class:`ProvenanceRecord` so that every node and edge in the
    assembled graph can be audited.
    """

    def __init__(self) -> None:
        self.records: Dict[str, ProvenanceRecord] = {}

    def record(self, element_id: str, provenance: ProvenanceRecord) -> None:
        """Record provenance for a graph element.

        Args:
            element_id: Unique identifier of the graph element.
            provenance: Provenance record to store.
        """
        self.records[element_id] = provenance

    def get(self, element_id: str) -> Optional[ProvenanceRecord]:
        """Retrieve provenance for an element.

        Args:
            element_id: Unique identifier of the graph element.

        Returns:
            The stored :class:`ProvenanceRecord`, or ``None`` if not found.
        """
        return self.records.get(element_id)

    def verify(self, element_id: str) -> bool:
        """Verify that an element has valid provenance.

        An element is considered verified when:
        * A record exists for ``element_id``.
        * The record has a non-empty ``source_document``.
        * The record has a non-empty ``extraction_method``.

        Args:
            element_id: Unique identifier of the graph element.

        Returns:
            ``True`` iff the element passes verification checks.
        """
        rec = self.records.get(element_id)
        if rec is None:
            return False
        return bool(rec.source_document) and bool(rec.extraction_method)

    def all_verified(self, element_ids: list[str]) -> bool:
        """Check whether every element in *element_ids* is verified.

        Args:
            element_ids: Iterable of element identifiers.

        Returns:
            ``True`` iff all elements pass :meth:`verify`.
        """
        return all(self.verify(eid) for eid in element_ids)
