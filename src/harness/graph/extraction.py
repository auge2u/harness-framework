"""Stage 1: Entity and relation extraction from documents."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional

from harness.agent_backend import AgentBackend, BackendResponse
from harness.graph.types import Entity, ExtractedGraph, Relation


# ---------------------------------------------------------------------------
# Pattern-based extraction dictionaries for deterministic MockBackend tests
# ---------------------------------------------------------------------------

_APOLLO_CORPUS_PATTERNS: Dict[str, tuple[str, str]] = {
    # People
    "Neil Armstrong": ("PERSON", "American astronaut and the first person to walk on the Moon."),
    "Buzz Aldrin": ("PERSON", "American astronaut and the second person to walk on the Moon."),
    "Michael Collins": ("PERSON", "American astronaut who orbited the Moon during Apollo 11."),
    "John F. Kennedy": ("PERSON", "35th President of the United States who set the Moon landing goal."),
    "Wernher von Braun": ("PERSON", "German-American aerospace engineer and chief architect of the Saturn V rocket."),
    "Gene Kranz": ("PERSON", "NASA flight director during the Apollo program."),
    # Organizations
    "NASA": ("ORGANIZATION", "National Aeronautics and Space Administration, the US space agency."),
    "JPL": ("ORGANIZATION", "Jet Propulsion Laboratory, a NASA research center."),
    "MIT": ("ORGANIZATION", "Massachusetts Institute of Technology."),
    # Products / Technology
    "Apollo 11": ("MISSION", "First crewed mission to land on the Moon, July 1969."),
    "Apollo 12": ("MISSION", "Second crewed mission to land on the Moon, November 1969."),
    "Saturn V": ("VEHICLE", "Multistage rocket used to launch Apollo missions."),
    "Lunar Module": ("VEHICLE", "Spacecraft that landed astronauts on the Moon."),
    "Command Module": ("VEHICLE", "Spacecraft that orbited the Moon while the Lunar Module descended."),
    "Eagle": ("VEHICLE", "Name of the Apollo 11 Lunar Module."),
    "Columbia": ("VEHICLE", "Name of the Apollo 11 Command Module."),
    # Locations
    "Moon": ("LOCATION", "Earth's only natural satellite."),
    "Kennedy Space Center": ("LOCATION", "NASA launch facility in Florida."),
    "Sea of Tranquility": ("LOCATION", "Lunar mare where Apollo 11 landed."),
    "Houston": ("LOCATION", "Home of NASA's Mission Control Center."),
}

_RELATION_PATTERNS: List[tuple[str, str, str]] = [
    # (regex, predicate, direction)
    (r"(\w+(?:\s+\w+){0,3})\s+launched\s+from\s+(\w+(?:\s+\w+){0,3})", "launched from", "source_target"),
    (r"(\w+(?:\s+\w+){0,3})\s+landed\s+on\s+(\w+(?:\s+\w+){0,3})", "landed on", "source_target"),
    (r"(\w+(?:\s+\w+){0,3})\s+walked\s+on\s+(\w+(?:\s+\w+){0,3})", "walked on", "source_target"),
    (r"(\w+(?:\s+\w+){0,3})\s+orbited\s+(\w+(?:\s+\w+){0,3})", "orbited", "source_target"),
    (r"(\w+(?:\s+\w+){0,3})\s+designed\s+(\w+(?:\s+\w+){0,3})", "designed", "source_target"),
    (r"(\w+(?:\s+\w+){0,3})\s+built\s+(\w+(?:\s+\w+){0,3})", "built", "source_target"),
    (r"(\w+(?:\s+\w+){0,3})\s+commanded\s+(\w+(?:\s+\w+){0,3})", "commanded", "source_target"),
    (r"(\w+(?:\s+\w+){0,3})\s+directed\s+(\w+(?:\s+\w+){0,3})", "directed", "source_target"),
    (r"(\w+(?:\s+\w+){0,3})\s+worked\s+for\s+(\w+(?:\s+\w+){0,3})", "worked for", "source_target"),
    (r"(\w+(?:\s+\w+){0,3})\s+set\s+the\s+goal\s+of\s+(\w+(?:\s+\w+){0,3})", "set goal of", "source_target"),
    (r"(\w+(?:\s+\w+){0,3})\s+was\s+the\s+mission\s+control\s+for\s+(\w+(?:\s+\w+){0,3})", "mission control for", "source_target"),
]


def _extract_pattern_entities(document: str) -> List[Entity]:
    """Extract entities by matching known patterns in the document text."""
    entities: List[Entity] = []
    seen: set[str] = set()
    for name, (etype, desc) in _APOLLO_CORPUS_PATTERNS.items():
        if name.lower() in document.lower() and name not in seen:
            seen.add(name)
            entities.append(
                Entity(
                    entity_id=f"ent_{name.lower().replace(' ', '_')}",
                    name=name,
                    type=etype,
                    description=desc,
                    aliases=[name],
                )
            )
    return entities


def _extract_pattern_relations(document: str, entities: List[Entity]) -> List[Relation]:
    """Extract relations using regex patterns over the document text."""
    relations: List[Relation] = []
    entity_names = {e.name for e in entities}

    for pattern, predicate, direction in _RELATION_PATTERNS:
        for match in re.finditer(pattern, document, re.IGNORECASE):
            if direction == "source_target":
                source = match.group(1)
                target = match.group(2)
            else:
                continue
            # Only create relation if both source and target are known entities
            # or can be matched to known entities
            src_matched = _match_to_entity(source, entity_names)
            tgt_matched = _match_to_entity(target, entity_names)
            if src_matched and tgt_matched:
                relations.append(
                    Relation(
                        relation_id=f"rel_{uuid.uuid4().hex[:8]}",
                        source=src_matched,
                        predicate=predicate,
                        target=tgt_matched,
                        confidence=0.85,
                        provenance={"pattern": pattern, "matched_text": match.group(0)},
                    )
                )

    # Also infer relations from simple sentence patterns: "X was the Y of Z"
    for ent in entities:
        # Check for "X was the commander of Y" style
        r = re.search(
            rf"{re.escape(ent.name)}\s+was\s+the\s+(\w+)\s+of\s+(\w+(?:\s+\w+){0,2})",
            document,
            re.IGNORECASE,
        )
        if r:
            role = r.group(1)
            target_name = r.group(2)
            tgt_matched = _match_to_entity(target_name, entity_names)
            if tgt_matched:
                relations.append(
                    Relation(
                        relation_id=f"rel_{uuid.uuid4().hex[:8]}",
                        source=ent.name,
                        predicate=f"was {role} of",
                        target=tgt_matched,
                        confidence=0.9,
                        provenance={"pattern": "was_the_X_of", "matched_text": r.group(0)},
                    )
                )

    return relations


def _match_to_entity(text: str, entity_names: set[str]) -> str:
    """Match extracted text fragment to a known entity name.

    Returns the exact entity name if found (case-insensitive), otherwise
    returns the original text.
    """
    text_lower = text.lower()
    for name in entity_names:
        if name.lower() == text_lower:
            return name
    # Substring match
    for name in entity_names:
        if name.lower() in text_lower or text_lower in name.lower():
            return name
    return text


class GraphExtractor:
    """Extracts entities and relations from documents using structured outputs.

    When backed by :class:`~harness.agent_backend.MockBackend`, the extractor
    falls back to deterministic pattern-based extraction so that tests can run
    without a live LLM.
    """

    def __init__(
        self,
        backend: AgentBackend,
        entity_types: Optional[List[str]] = None,
        relation_types: Optional[List[str]] = None,
    ):
        self.backend = backend
        self.entity_types = entity_types or ["PERSON", "ORGANIZATION", "LOCATION", "MISSION", "VEHICLE"]
        self.relation_types = relation_types or [
            "launched from", "landed on", "walked on", "orbited",
            "designed", "built", "commanded", "directed", "worked for",
        ]

    def extract(self, document: str, document_id: str = "") -> ExtractedGraph:
        """Extract entities and relations from a single document.

        If the backend is a :class:`~harness.agent_backend.MockBackend`,
        pattern-based extraction is used so no LLM call is required.
        Otherwise a structured LLM extraction prompt is sent.

        Args:
            document: Raw text of the document.
            document_id: Optional identifier for the document.

        Returns:
            An :class:`ExtractedGraph` containing entities and relations.
        """
        from harness.agent_backend import MockBackend

        if isinstance(self.backend, MockBackend):
            return self._extract_mock(document, document_id)

        return self._extract_llm(document, document_id)

    def _extract_mock(self, document: str, document_id: str) -> ExtractedGraph:
        """Deterministic pattern-based extraction for mock backend."""
        entities = _extract_pattern_entities(document)
        relations = _extract_pattern_relations(document, entities)
        return ExtractedGraph(
            entities=entities,
            relations=relations,
            source_document=document_id,
            extraction_metadata={
                "method": "pattern",
                "backend": "mock",
                "entity_count": len(entities),
                "relation_count": len(relations),
            },
        )

    def _extract_llm(self, document: str, document_id: str) -> ExtractedGraph:
        """Structured LLM extraction via backend.complete()."""
        prompt = self._build_extraction_prompt(document)
        from harness.agent_backend import Message
        response: BackendResponse = self.backend.complete(
            messages=[Message(role="user", content=prompt)],
        )
        return self._parse_llm_response(response.content, document_id)

    def _build_extraction_prompt(self, document: str) -> str:
        """Build a JSON-mode extraction prompt."""
        entity_types_str = ", ".join(self.entity_types)
        relation_types_str = ", ".join(self.relation_types)
        return (
            f"Extract entities and relations from the following text.\n\n"
            f"Allowed entity types: {entity_types_str}\n"
            f"Allowed relation types: {relation_types_str}\n\n"
            f"Text:\n{document}\n\n"
            f"Return ONLY valid JSON with keys: entities (list of {{id, name, type, description}}) "
            f"and relations (list of {{source, predicate, target, confidence}})."
        )

    def _parse_llm_response(self, content: str, document_id: str) -> ExtractedGraph:
        """Parse JSON response from LLM into ExtractedGraph."""
        import re

        try:
            # Strip markdown code fences if present
            cleaned = content.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
                cleaned = re.sub(r"\s*```\s*$", "", cleaned)
            data = json.loads(cleaned)
        except Exception:
            # If parsing fails, return empty graph
            return ExtractedGraph(
                source_document=document_id,
                extraction_metadata={"method": "llm", "parse_error": True},
            )

        entities = [
            Entity(
                entity_id=e.get("id", f"ent_{uuid.uuid4().hex[:8]}"),
                name=e.get("name", ""),
                type=e.get("type", "UNKNOWN"),
                description=e.get("description", ""),
            )
            for e in data.get("entities", [])
        ]
        relations = [
            Relation(
                relation_id=f"rel_{uuid.uuid4().hex[:8]}",
                source=r.get("source", ""),
                predicate=r.get("predicate", ""),
                target=r.get("target", ""),
                confidence=float(r.get("confidence", 1.0)),
            )
            for r in data.get("relations", [])
        ]
        return ExtractedGraph(
            entities=entities,
            relations=relations,
            source_document=document_id,
            extraction_metadata={"method": "llm", "parse_error": False},
        )

    def extract_batch(self, documents: List[str]) -> List[ExtractedGraph]:
        """Extract from multiple documents.

        Args:
            documents: List of document texts.

        Returns:
            List of :class:`ExtractedGraph` objects in the same order.
        """
        return [self.extract(doc, document_id=f"doc_{i}") for i, doc in enumerate(documents)]
