"""Tests for harness.store.snapshots and its HarnessLineage integration."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import pytest

from harness.analysis.lineage import HarnessLineage
from harness.core.config import HarnessConfig
from harness.reflex.rubrics import Rubric
from harness.store.snapshots import (
    ContentAddressedStore,
    SnapshotNotFoundError,
    SnapshotRef,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    return ContentAddressedStore(tmp_path / "snapshots")


@pytest.fixture
def sample_rubric() -> Rubric:
    return Rubric(
        rubric_id="rub-1",
        name="quality",
        version="1.0.0",
        criteria=["clear", "correct"],
        keyword_signals={"good": 1.0},
    )


@dataclass
class _Widget:
    """Simple dataclass artifact for roundtrip tests."""

    name: str
    count: int = 0
    tags: List[str] = field(default_factory=list)


class _PlainObject:
    """Object exposing only ``__dict__``."""

    def __init__(self) -> None:
        self.alpha = 1
        self.beta = "two"


def _blob_files(root: Path) -> List[Path]:
    return [p for p in root.rglob("*.json") if p.name != "index.jsonl"]


# ---------------------------------------------------------------------------
# put / get roundtrip
# ---------------------------------------------------------------------------

class TestPutGet:
    def test_put_get_roundtrip_dict(self, store):
        artifact = {"name": "cfg", "values": [1, 2, 3], "nested": {"a": True}}
        ref = store.put(artifact, artifact_type="config")
        assert isinstance(ref, SnapshotRef)
        assert store.get(ref.content_hash) == artifact

    def test_put_get_roundtrip_dataclass(self, store):
        widget = _Widget(name="w", count=3, tags=["x", "y"])
        ref = store.put(widget, artifact_type="widget")
        assert store.get(ref.content_hash) == asdict(widget)

    def test_put_get_roundtrip_rubric_dataclass(self, store, sample_rubric):
        ref = store.put(sample_rubric, artifact_type="rubric")
        assert store.get(ref.content_hash) == asdict(sample_rubric)

    def test_put_object_with_dunder_dict(self, store):
        obj = _PlainObject()
        ref = store.put(obj, artifact_type="obj")
        assert store.get(ref.content_hash) == {"alpha": 1, "beta": "two"}

    def test_put_unsupported_type_raises(self, store):
        with pytest.raises(TypeError):
            store.put(42, artifact_type="nope")

    def test_ref_fields(self, store):
        ref = store.put({"k": "v"}, artifact_type="config",
                        metadata={"origin": "test"})
        assert len(ref.content_hash) == 64
        assert ref.artifact_type == "config"
        assert ref.size_bytes > 0
        assert ref.metadata == {"origin": "test"}
        assert ref.path == f"{ref.content_hash[:2]}/{ref.content_hash}.json"


# ---------------------------------------------------------------------------
# Determinism and dedupe
# ---------------------------------------------------------------------------

class TestDeterminismAndDedupe:
    def test_same_content_different_key_order_same_hash(self, store):
        ref1 = store.put({"a": 1, "b": {"x": 1, "y": 2}}, artifact_type="config")
        ref2 = store.put({"b": {"y": 2, "x": 1}, "a": 1}, artifact_type="config")
        assert ref1.content_hash == ref2.content_hash

    def test_different_content_different_hash(self, store):
        ref1 = store.put({"a": 1}, artifact_type="config")
        ref2 = store.put({"a": 2}, artifact_type="config")
        assert ref1.content_hash != ref2.content_hash

    def test_dedupe_single_blob_same_ref(self, store, tmp_path):
        artifact = {"dedupe": "me"}
        ref1 = store.put(artifact, artifact_type="config")
        ref2 = store.put(artifact, artifact_type="config")
        assert ref1.content_hash == ref2.content_hash
        root = tmp_path / "snapshots"
        assert len(_blob_files(root)) == 1
        assert store.stats()["dedupe_hits"] == 1

    def test_dedupe_does_not_rewrite_blob(self, store, tmp_path):
        artifact = {"stable": True}
        ref = store.put(artifact, artifact_type="config")
        blob = tmp_path / "snapshots" / ref.path
        before = (blob.stat().st_mtime_ns, blob.read_bytes())
        time.sleep(0.01)
        store.put(artifact, artifact_type="config")
        after = (blob.stat().st_mtime_ns, blob.read_bytes())
        assert before == after

    def test_dedupe_hits_accumulate(self, store):
        artifact = {"x": [1, 2, 3]}
        store.put(artifact, artifact_type="config")
        store.put(artifact, artifact_type="config")
        store.put(artifact, artifact_type="config")
        assert store.stats()["dedupe_hits"] == 2


# ---------------------------------------------------------------------------
# get / has / get_typed
# ---------------------------------------------------------------------------

class TestGetHasTyped:
    def test_get_missing_raises(self, store):
        with pytest.raises(SnapshotNotFoundError):
            store.get("0" * 64)

    def test_has(self, store):
        ref = store.put({"a": 1}, artifact_type="config")
        assert store.has(ref.content_hash) is True
        assert store.has("f" * 64) is False

    def test_get_typed_match(self, store):
        artifact = {"cfg": 1}
        ref = store.put(artifact, artifact_type="config")
        assert store.get_typed(ref.content_hash, "config") == artifact

    def test_get_typed_mismatch_raises(self, store):
        ref = store.put({"cfg": 1}, artifact_type="config")
        with pytest.raises(TypeError):
            store.get_typed(ref.content_hash, "rubric")

    def test_get_typed_missing_raises(self, store):
        with pytest.raises(SnapshotNotFoundError):
            store.get_typed("0" * 64, "config")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

class TestList:
    def test_list_empty(self, store):
        assert store.list() == []

    def test_list_filters_by_type(self, store):
        store.put({"a": 1}, artifact_type="config")
        store.put({"b": 2}, artifact_type="rubric")
        store.put({"c": 3}, artifact_type="config")
        configs = store.list(artifact_type="config")
        assert len(configs) == 2
        assert all(r.artifact_type == "config" for r in configs)
        assert len(store.list()) == 3

    def test_list_newest_last(self, store):
        refs = [
            store.put({"i": i}, artifact_type="config") for i in range(3)
        ]
        listed = store.list()
        assert [r.content_hash for r in listed] == [r.content_hash for r in refs]

    def test_list_excludes_deleted(self, store):
        ref1 = store.put({"a": 1}, artifact_type="config")
        store.put({"b": 2}, artifact_type="config")
        store.delete(ref1.content_hash)
        listed = store.list()
        assert len(listed) == 1
        assert listed[0].content_hash != ref1.content_hash

    def test_list_returns_snapshot_refs_with_metadata(self, store):
        ref = store.put({"a": 1}, artifact_type="rubric",
                        metadata={"name": "quality"})
        listed = store.list(artifact_type="rubric")
        assert listed[0].metadata == {"name": "quality"}
        assert listed[0].path == ref.path
        assert listed[0].size_bytes == ref.size_bytes


# ---------------------------------------------------------------------------
# delete / verify
# ---------------------------------------------------------------------------

class TestDeleteVerify:
    def test_delete_removes_blob(self, store):
        ref = store.put({"a": 1}, artifact_type="config")
        assert store.delete(ref.content_hash) is True
        assert store.has(ref.content_hash) is False
        with pytest.raises(SnapshotNotFoundError):
            store.get(ref.content_hash)

    def test_delete_missing_returns_false(self, store):
        assert store.delete("0" * 64) is False

    def test_delete_leaves_tombstone_in_index(self, store, tmp_path):
        ref = store.put({"a": 1}, artifact_type="config")
        store.delete(ref.content_hash)
        index = tmp_path / "snapshots" / "index.jsonl"
        entries = [json.loads(l) for l in index.read_text().splitlines() if l.strip()]
        tombstones = [
            e for e in entries
            if e.get("action") == "delete" and e.get("hash") == ref.content_hash
        ]
        assert len(tombstones) == 1

    def test_verify_intact(self, store):
        ref = store.put({"intact": True}, artifact_type="config")
        assert store.verify(ref.content_hash) is True

    def test_verify_detects_corruption(self, store, tmp_path):
        ref = store.put({"fragile": 1}, artifact_type="config")
        blob = tmp_path / "snapshots" / ref.path
        blob.write_bytes(b'{"fragile": 2}')
        assert store.verify(ref.content_hash) is False

    def test_verify_missing_returns_false(self, store):
        assert store.verify("0" * 64) is False


# ---------------------------------------------------------------------------
# Layout / persistence / stats
# ---------------------------------------------------------------------------

class TestLayoutPersistenceStats:
    def test_hash_prefix_sharding(self, store, tmp_path):
        ref = store.put({"shard": "me"}, artifact_type="config")
        blob = tmp_path / "snapshots" / ref.content_hash[:2] / f"{ref.content_hash}.json"
        assert blob.exists()
        assert ref.path == f"{ref.content_hash[:2]}/{ref.content_hash}.json"

    def test_index_appended_per_put(self, store, tmp_path):
        store.put({"a": 1}, artifact_type="config")
        store.put({"b": 2}, artifact_type="rubric")
        index = tmp_path / "snapshots" / "index.jsonl"
        entries = [json.loads(l) for l in index.read_text().splitlines() if l.strip()]
        assert len(entries) == 2
        assert {e["artifact_type"] for e in entries} == {"config", "rubric"}
        assert all("stored_at" in e and "hash" in e for e in entries)

    def test_index_survives_reopen(self, tmp_path):
        root = tmp_path / "snapshots"
        store1 = ContentAddressedStore(root)
        ref1 = store1.put({"a": 1}, artifact_type="config")
        store1.put({"b": 2}, artifact_type="rubric")

        store2 = ContentAddressedStore(root)
        assert store2.has(ref1.content_hash)
        assert store2.get(ref1.content_hash) == {"a": 1}
        assert len(store2.list()) == 2
        assert len(store2.list(artifact_type="rubric")) == 1

    def test_stats_counts_and_bytes(self, store):
        store.put({"a": 1}, artifact_type="config")
        store.put({"b": 2}, artifact_type="rubric")
        store.put({"c": 3}, artifact_type="rubric")
        stats = store.stats()
        assert stats["total_snapshots"] == 3
        assert stats["counts_by_type"] == {"config": 1, "rubric": 2}
        assert stats["total_bytes"] > 0
        assert stats["dedupe_hits"] == 0

    def test_stats_after_delete(self, store):
        ref = store.put({"a": 1}, artifact_type="config")
        store.put({"b": 2}, artifact_type="config")
        store.delete(ref.content_hash)
        stats = store.stats()
        assert stats["total_snapshots"] == 1
        assert stats["counts_by_type"] == {"config": 1}


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_concurrent_puts_no_corruption(self, store, tmp_path):
        shared = {"shared": list(range(10))}
        errors: List[BaseException] = []
        refs: List[SnapshotRef] = []
        lock = threading.Lock()

        def worker(i: int) -> None:
            try:
                if i % 2 == 0:
                    ref = store.put(shared, artifact_type="config")
                else:
                    ref = store.put({"unique": i}, artifact_type="config")
                with lock:
                    refs.append(ref)
            except BaseException as exc:  # pragma: no cover
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(refs) == 20
        # 10 identical + 10 unique => 11 distinct blobs
        assert len(_blob_files(tmp_path / "snapshots")) == 11
        # 9 of the shared-content puts were dedupe hits
        assert store.stats()["dedupe_hits"] == 9
        # Every ref resolves and verifies
        for ref in refs:
            assert store.verify(ref.content_hash)
        # The shared artifact round-trips intact (this put is itself deduped)
        shared_ref = store.put(shared, artifact_type="config")
        assert store.get(shared_ref.content_hash) == shared
        assert shared_ref.content_hash in {r.content_hash for r in refs}


# ---------------------------------------------------------------------------
# HarnessLineage integration
# ---------------------------------------------------------------------------

class TestLineageIntegration:
    def _make_lineage(self, tmp_path, with_store: bool = True):
        store = (
            ContentAddressedStore(tmp_path / ".harness_lineage" / "snapshots")
            if with_store
            else None
        )
        lineage = HarnessLineage(
            str(tmp_path / ".harness_lineage"), snapshot_store=store
        )
        return lineage, store

    def test_commit_records_snapshot_hash(self, tmp_path, sample_config):
        lineage, store = self._make_lineage(tmp_path)
        node = lineage.commit(sample_config)
        assert node.snapshot_hash != ""
        assert store.has(node.snapshot_hash)
        assert store.get(node.snapshot_hash) == node.config_snapshot

    def test_checkout_by_hash_returns_config_dict(self, tmp_path, sample_config):
        lineage, _ = self._make_lineage(tmp_path)
        node = lineage.commit(sample_config)
        restored = lineage.checkout_by_hash(node.snapshot_hash)
        assert restored == sample_config.to_dict()

    def test_checkout_by_hash_roundtrip_config(self, tmp_path, sample_config):
        lineage, _ = self._make_lineage(tmp_path)
        node = lineage.commit(sample_config)
        config = HarnessConfig.from_dict(lineage.checkout_by_hash(node.snapshot_hash))
        assert config.to_dict() == sample_config.to_dict()

    def test_checkout_by_hash_missing_raises(self, tmp_path, sample_config):
        lineage, _ = self._make_lineage(tmp_path)
        lineage.commit(sample_config)
        with pytest.raises(SnapshotNotFoundError):
            lineage.checkout_by_hash("0" * 64)

    def test_snapshot_rubric(self, tmp_path, sample_config, sample_rubric):
        lineage, store = self._make_lineage(tmp_path)
        lineage.commit(sample_config)
        ref = lineage.snapshot_rubric(asdict(sample_rubric),
                                      metadata={"name": sample_rubric.name})
        assert ref.artifact_type == "rubric"
        assert store.get_typed(ref.content_hash, "rubric") == asdict(sample_rubric)
        assert store.list(artifact_type="rubric")[0].metadata == {"name": "quality"}

    def test_commit_dedupes_identical_configs(self, tmp_path, sample_config):
        lineage, store = self._make_lineage(tmp_path)
        node1 = lineage.commit(sample_config)
        # Force a distinct version by committing identical config again;
        # content is identical so the store dedupes.
        node2 = lineage.commit(sample_config)
        assert node1.snapshot_hash == node2.snapshot_hash
        assert store.stats()["counts_by_type"] == {"config": 1}
        assert store.stats()["dedupe_hits"] == 1

    def test_snapshot_hash_persisted_in_nodes_file(self, tmp_path, sample_config):
        lineage, _ = self._make_lineage(tmp_path)
        node = lineage.commit(sample_config)
        reread = lineage.get_node(node.version)
        assert reread is not None
        assert reread.snapshot_hash == node.snapshot_hash

    def test_without_store_behaviour_unchanged(self, tmp_path, sample_config):
        lineage, _ = self._make_lineage(tmp_path, with_store=False)
        node = lineage.commit(sample_config)
        assert node.snapshot_hash == ""
        assert lineage.checkout(node.version).to_dict() == sample_config.to_dict()

    def test_checkout_by_hash_requires_store(self, tmp_path, sample_config):
        lineage, _ = self._make_lineage(tmp_path, with_store=False)
        lineage.commit(sample_config)
        with pytest.raises(ValueError, match="snapshot store"):
            lineage.checkout_by_hash("0" * 64)

    def test_snapshot_rubric_requires_store(self, tmp_path):
        lineage, _ = self._make_lineage(tmp_path, with_store=False)
        with pytest.raises(ValueError, match="snapshot store"):
            lineage.snapshot_rubric({"rubric_id": "r", "name": "n"})
