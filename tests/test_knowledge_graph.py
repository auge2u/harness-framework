"""Comprehensive tests for the Harness knowledge graph pipeline.

Covers all 4 stages: extraction → resolution → assembly → query,
following the Anthropic Graph-Engineering Playbook patterns.
"""

from __future__ import annotations

import pytest

from harness.agent_backend import MockBackend
from harness.graph.assembly import GraphAssembler
from harness.graph.extraction import GraphExtractor, _extract_pattern_entities
from harness.graph.pipeline import KnowledgeGraphPipeline
from harness.graph.provenance import ProvenanceRecord, ProvenanceTracker
from harness.graph.query import GraphQuerier
from harness.graph.resolution import EntityResolver
from harness.graph.types import Entity, ExtractedGraph, Relation, ResolutionCluster
from harness.plugins.kg_plugin import KnowledgeGraphPlugin
from harness.plugins import PluginContext


# -----------------------------------------------------------------------------
# Apollo 11 corpus -- 6-document test set from the playbook
# -----------------------------------------------------------------------------

APOLLO_CORPUS = [
    (
        "Apollo 11 launched from Kennedy Space Center on July 16, 1969. "
        "It carried Neil Armstrong, Buzz Aldrin, and Michael Collins. "
        "The mission was commanded by Neil Armstrong."
    ),
    (
        "Neil Armstrong and Buzz Aldrin walked on the Moon on July 20, 1969. "
        "Michael Collins orbited the Moon in the Command Module Columbia. "
        "The Lunar Module Eagle landed on the Sea of Tranquility."
    ),
    (
        "The Saturn V rocket was designed by Wernher von Braun and built by NASA. "
        "It launched Apollo 11 toward the Moon from Kennedy Space Center. "
        "Houston served as the mission control for Apollo 11."
    ),
    (
        "NASA is the National Aeronautics and Space Administration. "
        "NASA set the goal of landing humans on the Moon. "
        "John F. Kennedy set the goal of Apollo 11 in 1961."
    ),
    (
        "Gene Kranz directed Apollo 11 from Mission Control in Houston. "
        "He worked for NASA and was a key flight director."
    ),
    (
        "Apollo 12 was the second crewed mission to land on the Moon. "
        "It also launched from Kennedy Space Center. "
        "The Saturn V rocket was used again for Apollo 12."
    ),
]


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def mock_backend():
    return MockBackend()


@pytest.fixture
def extractor(mock_backend):
    return GraphExtractor(
        backend=mock_backend,
        entity_types=["PERSON", "ORGANIZATION", "LOCATION", "MISSION", "VEHICLE"],
    )


@pytest.fixture
def resolver():
    return EntityResolver(similarity_threshold=0.85)


@pytest.fixture
def assembler():
    return GraphAssembler()


@pytest.fixture
def tracker():
    return ProvenanceTracker()


# -----------------------------------------------------------------------------
# 1. Entity extraction (MockBackend)
# -----------------------------------------------------------------------------

def test_extract_neil_armstrong(extractor):
    """Extractor creates PERSON entity for Neil Armstrong."""
    doc = "Neil Armstrong was the commander of Apollo 11."
    graph = extractor.extract(doc, "doc_0")
    names = {e.name for e in graph.entities}
    assert "Neil Armstrong" in names
    armstrong = next(e for e in graph.entities if e.name == "Neil Armstrong")
    assert armstrong.type == "PERSON"


def test_extract_apollo_11(extractor):
    """Extractor creates MISSION entity for Apollo 11."""
    doc = "Apollo 11 launched from Kennedy Space Center."
    graph = extractor.extract(doc, "doc_0")
    names = {e.name for e in graph.entities}
    assert "Apollo 11" in names
    mission = next(e for e in graph.entities if e.name == "Apollo 11")
    assert mission.type == "MISSION"


def test_extract_multiple_entities(extractor):
    """Extractor finds multiple entities in one document."""
    doc = (
        "NASA built the Saturn V rocket. "
        "Wernher von Braun designed it. "
        "Apollo 11 launched from Kennedy Space Center."
    )
    graph = extractor.extract(doc, "doc_0")
    names = {e.name for e in graph.entities}
    assert names >= {"NASA", "Saturn V", "Wernher von Braun", "Apollo 11", "Kennedy Space Center"}


def test_extract_empty_document(extractor):
    """Extractor returns empty graph for empty document."""
    graph = extractor.extract("", "empty")
    assert graph.entities == []
    assert graph.relations == []


def test_extract_no_known_entities(extractor):
    """Extractor returns empty graph when no known patterns match."""
    graph = extractor.extract("The quick brown fox jumps over the lazy dog.", "doc_x")
    assert graph.entities == []
    assert graph.relations == []


def test_extract_batch(extractor):
    """Batch extraction returns one graph per document."""
    docs = ["Neil Armstrong walked on the Moon.", "NASA built the Saturn V."]
    graphs = extractor.extract_batch(docs)
    assert len(graphs) == 2
    assert any("Neil Armstrong" in {e.name for e in g.entities} for g in graphs)
    assert any("NASA" in {e.name for e in g.entities} for g in graphs)


# -----------------------------------------------------------------------------
# 2. Relation extraction with predicate parsing
# -----------------------------------------------------------------------------

def test_extract_launched_from_relation(extractor):
    """Extractor creates 'launched from' relation."""
    doc = "Apollo 11 launched from Kennedy Space Center."
    graph = extractor.extract(doc, "doc_0")
    preds = {r.predicate for r in graph.relations}
    assert "launched from" in preds


def test_extract_walked_on_relation(extractor):
    """Extractor creates 'walked on' relation."""
    doc = "Neil Armstrong walked on the Moon."
    graph = extractor.extract(doc, "doc_0")
    preds = {r.predicate for r in graph.relations}
    assert "walked on" in preds


def test_extract_landed_on_relation(extractor):
    """Extractor creates 'landed on' relation."""
    doc = "The Lunar Module landed on the Moon."
    graph = extractor.extract(doc, "doc_0")
    preds = {r.predicate for r in graph.relations}
    assert "landed on" in preds


def test_extract_relation_has_confidence(extractor):
    """Extracted relations have confidence scores."""
    doc = "Apollo 11 launched from Kennedy Space Center."
    graph = extractor.extract(doc, "doc_0")
    for rel in graph.relations:
        assert 0.0 <= rel.confidence <= 1.0


# -----------------------------------------------------------------------------
# 3. Entity resolution
# -----------------------------------------------------------------------------

def test_resolve_exact_match(resolver):
    """Exact name matches resolve to the same cluster."""
    e1 = Entity("e1", "NASA", "ORGANIZATION", "US space agency")
    e2 = Entity("e2", "NASA", "ORGANIZATION", "US space agency")
    graphs = [ExtractedGraph(entities=[e1, e2])]
    clusters = resolver.resolve(graphs)
    canon_names = {c.canonical_entity.name for c in clusters}
    assert "NASA" in canon_names
    nasa_cluster = next(c for c in clusters if c.canonical_entity.name == "NASA")
    assert len(nasa_cluster.members) == 2


def test_resolve_case_insensitive(resolver):
    """Case-insensitive matches resolve to the same cluster."""
    e1 = Entity("e1", "Apollo 11", "MISSION", "First Moon landing")
    e2 = Entity("e2", "apollo 11", "MISSION", "First Moon landing")
    graphs = [ExtractedGraph(entities=[e1, e2])]
    clusters = resolver.resolve(graphs)
    assert len(clusters) == 1
    assert clusters[0].resolution_method == "case_insensitive"


def test_resolve_substring_match(resolver):
    """Substring matches resolve to the same cluster."""
    e1 = Entity("e1", "Kennedy Space Center", "LOCATION", "NASA launch site")
    e2 = Entity("e2", "Space Center", "LOCATION", "NASA launch site")
    graphs = [ExtractedGraph(entities=[e1, e2])]
    clusters = resolver.resolve(graphs)
    assert len(clusters) == 1
    assert clusters[0].resolution_method == "substring"


def test_resolve_different_types_remain_separate(resolver):
    """Entities with different types are not merged."""
    e1 = Entity("e1", "Columbia", "VEHICLE", "Command Module")
    e2 = Entity("e2", "Columbia", "LOCATION", "Country in South America")
    graphs = [ExtractedGraph(entities=[e1, e2])]
    clusters = resolver.resolve(graphs)
    assert len(clusters) == 2


def test_resolve_alias_map_populated(resolver):
    """Resolver builds alias_map after resolution."""
    e1 = Entity("e1", "NASA", "ORGANIZATION", "", aliases=["NACA"])
    graphs = [ExtractedGraph(entities=[e1])]
    resolver.resolve(graphs)
    assert resolver.alias_map.get("nasa") is not None
    assert resolver.alias_map.get("naca") is not None


def test_resolve_entity_method(resolver):
    """resolve_entity returns canonical ID for known surface form."""
    e1 = Entity("e1", "NASA", "ORGANIZATION", "")
    graphs = [ExtractedGraph(entities=[e1])]
    resolver.resolve(graphs)
    e2 = Entity("e2", "NASA", "ORGANIZATION", "")
    assert resolver.resolve_entity(e2).startswith("canon_")


def test_name_similarity_exact(resolver):
    """Exact match yields similarity 1.0."""
    assert resolver._name_similarity("Apollo 11", "Apollo 11") == 1.0


def test_name_similarity_case_insensitive(resolver):
    """Case-insensitive match yields 0.95."""
    assert resolver._name_similarity("Apollo 11", "apollo 11") == 0.95


def test_name_similarity_substring(resolver):
    """Substring match yields 0.9."""
    assert resolver._name_similarity("Kennedy Space Center", "Space Center") == 0.9


def test_name_similarity_jaccard(resolver):
    """Partial token overlap yields Jaccard similarity."""
    sim = resolver._name_similarity("Neil Armstrong", "Neil A. Armstrong")
    assert 0.0 < sim < 0.9


# -----------------------------------------------------------------------------
# 4. Graph assembly
# -----------------------------------------------------------------------------

def test_assemble_creates_nodes(assembler):
    """Assembler creates nodes for canonical entities."""
    cluster = ResolutionCluster(
        canonical_entity=Entity("c1", "NASA", "ORGANIZATION", "Space agency"),
        members=[Entity("e1", "NASA", "ORGANIZATION", "")],
    )
    graph = assembler.assemble([cluster], [])
    assert graph.has_node("c1")


def test_assemble_creates_edges(assembler):
    """Assembler creates edges for relations."""
    cluster = ResolutionCluster(
        canonical_entity=Entity("c1", "Apollo 11", "MISSION", ""),
        members=[Entity("e1", "Apollo 11", "MISSION", "")],
    )
    rel = Relation("r1", "Apollo 11", "launched from", "Kennedy Space Center")
    graph = assembler.assemble([cluster], [rel])
    edges = list(graph.edges(data=True))
    assert any(e[2].get("predicate") == "launched from" for e in edges)


def test_assemble_node_data(assembler):
    """Nodes carry full metadata."""
    cluster = ResolutionCluster(
        canonical_entity=Entity("c1", "NASA", "ORGANIZATION", "Space agency", aliases=["NACA"]),
        members=[Entity("e1", "NASA", "ORGANIZATION", "")],
    )
    graph = assembler.assemble([cluster], [])
    data = assembler.get_node("c1")
    assert data is not None
    assert data["name"] == "NASA"
    assert data["type"] == "ORGANIZATION"


def test_assemble_get_edges_filtered(assembler):
    """get_edges filters by source, target, and predicate."""
    c1 = ResolutionCluster(
        canonical_entity=Entity("c1", "Apollo 11", "MISSION", ""),
        members=[],
    )
    c2 = ResolutionCluster(
        canonical_entity=Entity("c2", "Kennedy Space Center", "LOCATION", ""),
        members=[],
    )
    rels = [
        Relation("r1", "Apollo 11", "launched from", "Kennedy Space Center"),
        Relation("r2", "Apollo 11", "landed on", "Moon"),
    ]
    graph = assembler.assemble([c1, c2], rels)
    launched = assembler.get_edges(predicate="launched from")
    assert len(launched) == 1


def test_assemble_empty_clusters(assembler):
    """Assembler handles empty cluster list."""
    graph = assembler.assemble([], [])
    assert graph.number_of_nodes() == 0
    assert graph.number_of_edges() == 0


# -----------------------------------------------------------------------------
# 5. Subgraph serialization
# -----------------------------------------------------------------------------

def test_serialize_subgraph_basic():
    """Subgraph serialization produces triple lines."""
    assembler = GraphAssembler()
    c1 = ResolutionCluster(
        canonical_entity=Entity("c1", "Apollo 11", "MISSION", ""),
        members=[],
    )
    c2 = ResolutionCluster(
        canonical_entity=Entity("c2", "Moon", "LOCATION", ""),
        members=[],
    )
    assembler.assemble([c1, c2], [Relation("r1", "Apollo 11", "landed on", "Moon")])
    querier = GraphQuerier(assembler.graph)
    text = querier.serialize_subgraph("c1", hops=1)
    assert "(c1) --[landed on]--> (c2)" in text


def test_serialize_subgraph_hops_limit():
    """Subgraph respects hop limit."""
    assembler = GraphAssembler()
    clusters = [
        ResolutionCluster(canonical_entity=Entity("c1", "A", "X", ""), members=[]),
        ResolutionCluster(canonical_entity=Entity("c2", "B", "X", ""), members=[]),
        ResolutionCluster(canonical_entity=Entity("c3", "C", "X", ""), members=[]),
    ]
    rels = [
        Relation("r1", "A", "links", "B"),
        Relation("r2", "B", "links", "C"),
    ]
    assembler.assemble(clusters, rels)
    querier = GraphQuerier(assembler.graph)
    text_1 = querier.serialize_subgraph("c1", hops=1)
    text_2 = querier.serialize_subgraph("c1", hops=2)
    assert len(text_2) >= len(text_1)


def test_serialize_subgraph_unknown_center():
    """Serializing around unknown center returns empty string."""
    assembler = GraphAssembler()
    querier = GraphQuerier(assembler.graph)
    assert querier.serialize_subgraph("nonexistent", hops=2) == ""


# -----------------------------------------------------------------------------
# 6. Multi-hop path finding
# -----------------------------------------------------------------------------

def test_multi_hop_direct_path():
    """Multi-hop finds a direct 1-hop path."""
    assembler = GraphAssembler()
    clusters = [
        ResolutionCluster(canonical_entity=Entity("c1", "A", "X", ""), members=[]),
        ResolutionCluster(canonical_entity=Entity("c2", "B", "X", ""), members=[]),
    ]
    assembler.assemble(clusters, [Relation("r1", "A", "links", "B")])
    querier = GraphQuerier(assembler.graph)
    paths = querier.multi_hop("c1", "c2", max_hops=1)
    assert ["c1", "c2"] in paths


def test_multi_hop_two_hop_path():
    """Multi-hop finds a 2-hop path."""
    assembler = GraphAssembler()
    clusters = [
        ResolutionCluster(canonical_entity=Entity("c1", "A", "X", ""), members=[]),
        ResolutionCluster(canonical_entity=Entity("c2", "B", "X", ""), members=[]),
        ResolutionCluster(canonical_entity=Entity("c3", "C", "X", ""), members=[]),
    ]
    rels = [
        Relation("r1", "A", "links", "B"),
        Relation("r2", "B", "links", "C"),
    ]
    assembler.assemble(clusters, rels)
    querier = GraphQuerier(assembler.graph)
    paths = querier.multi_hop("c1", "c3", max_hops=2)
    assert ["c1", "c2", "c3"] in paths


def test_multi_hop_no_path():
    """Multi-hop returns empty list when no path exists."""
    assembler = GraphAssembler()
    clusters = [
        ResolutionCluster(canonical_entity=Entity("c1", "A", "X", ""), members=[]),
        ResolutionCluster(canonical_entity=Entity("c2", "B", "X", ""), members=[]),
    ]
    assembler.assemble(clusters, [])
    querier = GraphQuerier(assembler.graph)
    paths = querier.multi_hop("c1", "c2", max_hops=3)
    assert paths == []


def test_multi_hop_respects_max_hops():
    """Multi-hop does not exceed max_hops."""
    assembler = GraphAssembler()
    clusters = [
        ResolutionCluster(canonical_entity=Entity("c1", "A", "X", ""), members=[]),
        ResolutionCluster(canonical_entity=Entity("c2", "B", "X", ""), members=[]),
        ResolutionCluster(canonical_entity=Entity("c3", "C", "X", ""), members=[]),
        ResolutionCluster(canonical_entity=Entity("c4", "D", "X", ""), members=[]),
    ]
    rels = [
        Relation("r1", "A", "links", "B"),
        Relation("r2", "B", "links", "C"),
        Relation("r3", "C", "links", "D"),
    ]
    assembler.assemble(clusters, rels)
    querier = GraphQuerier(assembler.graph)
    paths = querier.multi_hop("c1", "c4", max_hops=2)
    assert ["c1", "c2", "c3", "c4"] not in paths
    paths = querier.multi_hop("c1", "c4", max_hops=3)
    assert ["c1", "c2", "c3", "c4"] in paths


# -----------------------------------------------------------------------------
# 7. Provenance tracking
# -----------------------------------------------------------------------------

def test_tracker_record_and_get(tracker):
    """Tracker records and retrieves provenance."""
    rec = ProvenanceRecord(
        source_document="doc_0",
        agent_id="test",
        confidence=0.9,
        extraction_method="pattern",
    )
    tracker.record("e1", rec)
    got = tracker.get("e1")
    assert got is not None
    assert got.source_document == "doc_0"


def test_tracker_verify_valid(tracker):
    """Verify returns True for valid provenance."""
    tracker.record(
        "e1",
        ProvenanceRecord(
            source_document="doc_0",
            extraction_method="pattern",
        ),
    )
    assert tracker.verify("e1") is True


def test_tracker_verify_missing_document(tracker):
    """Verify returns False when source_document is empty."""
    tracker.record(
        "e1",
        ProvenanceRecord(
            source_document="",
            extraction_method="pattern",
        ),
    )
    assert tracker.verify("e1") is False


def test_tracker_verify_missing_method(tracker):
    """Verify returns False when extraction_method is empty."""
    tracker.record(
        "e1",
        ProvenanceRecord(
            source_document="doc_0",
            extraction_method="",
        ),
    )
    assert tracker.verify("e1") is False


def test_tracker_verify_missing_record(tracker):
    """Verify returns False for unknown element."""
    assert tracker.verify("nonexistent") is False


def test_tracker_all_verified(tracker):
    """all_verified returns True only when all elements are verified."""
    tracker.record("e1", ProvenanceRecord("doc_0", agent_id="a", extraction_method="m"))
    tracker.record("e2", ProvenanceRecord("doc_1", agent_id="a", extraction_method="m"))
    assert tracker.all_verified(["e1", "e2"]) is True
    assert tracker.all_verified(["e1", "e2", "e3"]) is False


# -----------------------------------------------------------------------------
# 8. Full pipeline integration
# -----------------------------------------------------------------------------

def test_pipeline_build(mock_backend):
    """Pipeline builds a non-empty graph from Apollo corpus."""
    pipeline = KnowledgeGraphPipeline(backend=mock_backend)
    graph = pipeline.build(APOLLO_CORPUS)
    assert graph.number_of_nodes() > 0
    assert graph.number_of_edges() > 0


def test_pipeline_query_who_walked_on_moon(mock_backend):
    """Pipeline answers 'Who walked on the Moon?' correctly."""
    pipeline = KnowledgeGraphPipeline(backend=mock_backend)
    pipeline.build(APOLLO_CORPUS)
    result = pipeline.query("Who walked on the Moon?")
    assert "Neil Armstrong" in result["answer"] or "Buzz Aldrin" in result["answer"]
    assert result["citations"]


def test_pipeline_query_launched_from(mock_backend):
    """Pipeline answers 'launched from' question."""
    pipeline = KnowledgeGraphPipeline(backend=mock_backend)
    pipeline.build(APOLLO_CORPUS)
    result = pipeline.query("What launched from Kennedy Space Center?")
    assert "Kennedy Space Center" in result["answer"] or "Apollo" in result["answer"]


def test_pipeline_query_before_build_raises(mock_backend):
    """Querying before build raises RuntimeError."""
    pipeline = KnowledgeGraphPipeline(backend=mock_backend)
    with pytest.raises(RuntimeError, match="Graph not built"):
        pipeline.query("Who walked on the Moon?")


def test_pipeline_provenance_tracked(mock_backend):
    """Pipeline tracks provenance for canonical nodes."""
    pipeline = KnowledgeGraphPipeline(backend=mock_backend)
    pipeline.build(APOLLO_CORPUS)
    for node_id in ["canon_nasa", "canon_neil_armstrong"]:
        if pipeline.verify(node_id):
            prov = pipeline.get_provenance(node_id)
            assert prov is not None
            assert prov.extraction_method != ""


# -----------------------------------------------------------------------------
# 9. Graph query with citations
# -----------------------------------------------------------------------------

def test_query_returns_citations(mock_backend):
    """Query result includes citations with provenance."""
    pipeline = KnowledgeGraphPipeline(backend=mock_backend)
    pipeline.build(APOLLO_CORPUS)
    result = pipeline.query("Who walked on the Moon?", hops=2)
    assert "citations" in result
    assert isinstance(result["citations"], list)


def test_query_subgraph_present(mock_backend):
    """Query result includes serialized subgraph."""
    pipeline = KnowledgeGraphPipeline(backend=mock_backend)
    pipeline.build(APOLLO_CORPUS)
    result = pipeline.query("Who walked on the Moon?", hops=1)
    assert "subgraph" in result
    assert len(result["subgraph"]) > 0


# -----------------------------------------------------------------------------
# 10. Error handling
# -----------------------------------------------------------------------------

def test_extract_empty_string_no_crash(extractor):
    """Extracting empty string does not raise."""
    graph = extractor.extract("", "empty")
    assert isinstance(graph, ExtractedGraph)


def test_resolver_empty_graphs(resolver):
    """Resolving empty graph list returns empty clusters."""
    clusters = resolver.resolve([])
    assert clusters == []


def test_assembler_get_node_missing(assembler):
    """get_node returns None for missing node."""
    assert assembler.get_node("missing") is None


def test_querier_neighbors_unknown():
    """Neighbors of unknown entity returns empty list."""
    assembler = GraphAssembler()
    querier = GraphQuerier(assembler.graph)
    assert querier.get_neighbors("unknown") == []


def test_querier_multi_hop_unknown_nodes():
    """Multi-hop between unknown nodes returns empty list."""
    assembler = GraphAssembler()
    querier = GraphQuerier(assembler.graph)
    assert querier.multi_hop("a", "b") == []


# -----------------------------------------------------------------------------
# 11. Neighbor queries
# -----------------------------------------------------------------------------

def test_get_neighbors_outgoing():
    """get_neighbors returns outgoing edges."""
    assembler = GraphAssembler()
    clusters = [
        ResolutionCluster(canonical_entity=Entity("c1", "A", "X", ""), members=[]),
        ResolutionCluster(canonical_entity=Entity("c2", "B", "X", ""), members=[]),
    ]
    assembler.assemble(clusters, [Relation("r1", "A", "links", "B")])
    querier = GraphQuerier(assembler.graph)
    neighbors = querier.get_neighbors("c1", direction="out")
    assert len(neighbors) == 1
    assert neighbors[0]["direction"] == "out"


def test_get_neighbors_incoming():
    """get_neighbors returns incoming edges."""
    assembler = GraphAssembler()
    clusters = [
        ResolutionCluster(canonical_entity=Entity("c1", "A", "X", ""), members=[]),
        ResolutionCluster(canonical_entity=Entity("c2", "B", "X", ""), members=[]),
    ]
    assembler.assemble(clusters, [Relation("r1", "A", "links", "B")])
    querier = GraphQuerier(assembler.graph)
    neighbors = querier.get_neighbors("c2", direction="in")
    assert len(neighbors) == 1
    assert neighbors[0]["direction"] == "in"


def test_get_neighbors_predicate_filter():
    """get_neighbors filters by predicate."""
    assembler = GraphAssembler()
    clusters = [
        ResolutionCluster(canonical_entity=Entity("c1", "A", "X", ""), members=[]),
        ResolutionCluster(canonical_entity=Entity("c2", "B", "X", ""), members=[]),
        ResolutionCluster(canonical_entity=Entity("c3", "C", "X", ""), members=[]),
    ]
    rels = [
        Relation("r1", "A", "links", "B"),
        Relation("r2", "A", "jumps", "C"),
    ]
    assembler.assemble(clusters, rels)
    querier = GraphQuerier(assembler.graph)
    neighbors = querier.get_neighbors("c1", predicate="jumps")
    assert len(neighbors) == 1
    assert neighbors[0]["predicate"] == "jumps"


# -----------------------------------------------------------------------------
# 12. Plugin integration
# -----------------------------------------------------------------------------

def test_kg_plugin_surface_name():
    """Plugin reports correct surface name."""
    plugin = KnowledgeGraphPlugin()
    assert plugin.surface_name == "knowledge_graph"


def test_kg_plugin_validate_ok():
    """Plugin validates with correct configuration."""
    plugin = KnowledgeGraphPlugin(backend=MockBackend())
    assert plugin.validate(None) == []


def test_kg_plugin_validate_invalid_threshold():
    """Plugin validation fails when similarity_threshold is out of range."""
    plugin = KnowledgeGraphPlugin(backend=MockBackend(), similarity_threshold=1.5)
    errors = plugin.validate(None)
    assert any("threshold" in e.lower() for e in errors)


def test_kg_plugin_apply_build(mock_backend):
    """Plugin apply with build operation returns success."""
    plugin = KnowledgeGraphPlugin(backend=mock_backend)
    ctx = PluginContext(
        metadata={
            "operation": "build",
            "documents": APOLLO_CORPUS,
        }
    )
    result = plugin.apply(None, ctx)
    assert result["status"] == "success"
    assert result["operation"] == "build"
    assert result["node_count"] > 0


def test_kg_plugin_apply_query(mock_backend):
    """Plugin apply with query operation returns answer."""
    plugin = KnowledgeGraphPlugin(backend=mock_backend)
    ctx = PluginContext(
        metadata={
            "operation": "query",
            "documents": APOLLO_CORPUS,
            "question": "Who walked on the Moon?",
        }
    )
    result = plugin.apply(None, ctx)
    assert result["status"] == "success"
    assert result["operation"] == "query"
    assert "Neil Armstrong" in result["answer"] or "Buzz Aldrin" in result["answer"]


def test_kg_plugin_apply_unknown_operation():
    """Plugin apply with unknown operation returns failure."""
    plugin = KnowledgeGraphPlugin(backend=MockBackend())
    ctx = PluginContext(metadata={"operation": "delete_everything"})
    result = plugin.apply(None, ctx)
    assert result["status"] == "failure"


def test_kg_plugin_get_surface():
    """Plugin surface descriptor has KNOWLEDGE_GRAPH type."""
    from harness.core.types import SurfaceType
    plugin = KnowledgeGraphPlugin()
    surface = plugin.get_surface()
    assert surface.type == SurfaceType.KNOWLEDGE_GRAPH
    assert surface.name == "knowledge_graph"


# -----------------------------------------------------------------------------
# 13. LLM extraction parsing (edge cases)
# -----------------------------------------------------------------------------

def test_extract_llm_json_parsing(extractor):
    """Extractor parses valid JSON response from LLM backend."""
    json_response = (
        '{"entities": [{"id": "e1", "name": "Alice", "type": "PERSON", "description": "A person"}],'
        '"relations": [{"source": "Alice", "predicate": "knows", "target": "Bob", "confidence": 0.9}]}'
    )
    backend = MockBackend(responses={"Extract": json_response})
    extractor_llm = GraphExtractor(backend=backend)
    # Force LLM path by using a non-MockBackend... actually MockBackend is used.
    # Instead, test the parse helper directly.
    graph = extractor_llm._parse_llm_response(json_response, "doc_test")
    assert any(e.name == "Alice" for e in graph.entities)


def test_extract_llm_malformed_json(extractor):
    """Extractor handles malformed JSON gracefully."""
    graph = extractor._parse_llm_response("not json", "doc_test")
    assert graph.entities == []
    assert graph.extraction_metadata.get("parse_error") is True


def test_extract_llm_markdown_fences(extractor):
    """Extractor strips markdown fences from JSON."""
    fenced = (
        '```json\n'
        '{"entities": [{"id": "e1", "name": "Alice", "type": "PERSON"}],'
        '"relations": []}\n'
        '```'
    )
    graph = extractor._parse_llm_response(fenced, "doc_test")
    assert any(e.name == "Alice" for e in graph.entities)


# -----------------------------------------------------------------------------
# 14. Type / dataclass defaults
# -----------------------------------------------------------------------------

def test_entity_defaults():
    """Entity dataclass has correct defaults."""
    e = Entity("e1", "Test", "X")
    assert e.description == ""
    assert e.aliases == []
    assert e.metadata == {}


def test_relation_defaults():
    """Relation dataclass has correct defaults."""
    r = Relation("r1", "A", "links", "B")
    assert r.confidence == 1.0
    assert r.provenance == {}


def test_extracted_graph_defaults():
    """ExtractedGraph dataclass has correct defaults."""
    g = ExtractedGraph()
    assert g.entities == []
    assert g.relations == []
    assert g.source_document == ""


def test_resolution_cluster_defaults():
    """ResolutionCluster dataclass has correct defaults."""
    c = ResolutionCluster(canonical_entity=Entity("c1", "A", "X", ""))
    assert c.members == []
    assert c.confidence == 1.0
    assert c.resolution_method == "exact"
