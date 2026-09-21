"""Tests for the immutable, hash-chained audit log (harness.audit)."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from harness.audit import (
    GENESIS_HASH,
    AuditEntry,
    AuditLog,
    AuditRecorder,
    ChainVerification,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_log(tmp_path: Path, name: str = "audit.jsonl") -> AuditLog:
    """Create a fresh AuditLog under tmp_path."""
    return AuditLog(tmp_path / name)


def _populate(log: AuditLog, n: int, actor: str = "tester") -> list:
    """Append n entries with deterministic payloads; return them."""
    return [
        log.append(actor, "validate_patch", surface=f"s{i}", payload={"msg": f"m{i:04d}"})
        for i in range(n)
    ]


def _read_lines(path: Path) -> list:
    """Return all non-empty lines of the log file."""
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _write_lines(path: Path, lines: list) -> None:
    """Overwrite the log file with the given lines."""
    path.write_text("".join(ln + "\n" for ln in lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Happy path & genesis
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_append_and_verify_chain_50(self, tmp_path):
        log = _make_log(tmp_path)
        entries = _populate(log, 50)
        assert len(log) == 50
        result = log.verify_chain()
        assert result.ok
        assert result.entries_checked == 50
        assert result.first_failure_seq is None
        assert result.failure_reason == ""
        # Sequence and linkage invariants
        for i, entry in enumerate(entries):
            assert entry.seq == i + 1
            assert len(entry.entry_hash) == 64
            if i == 0:
                assert entry.prev_hash == GENESIS_HASH
            else:
                assert entry.prev_hash == entries[i - 1].entry_hash

    def test_empty_log_verifies(self, tmp_path):
        log = _make_log(tmp_path)
        result = log.verify_chain()
        assert result.ok
        assert result.entries_checked == 0
        assert len(log) == 0
        assert log.last() is None
        assert log.verify_tail()

    def test_chain_verification_bool(self):
        assert bool(ChainVerification(True, 3)) is True
        assert bool(ChainVerification(False, 1, 2, "seq_gap")) is False

    def test_last_and_len(self, tmp_path):
        log = _make_log(tmp_path)
        assert log.last() is None
        _populate(log, 7)
        assert len(log) == 7
        last = log.last()
        assert last is not None
        assert last.seq == 7
        assert len(last.entry_hash) == 64

    def test_single_line_per_entry_jsonl(self, tmp_path):
        log = _make_log(tmp_path)
        _populate(log, 5)
        lines = _read_lines(tmp_path / "audit.jsonl")
        assert len(lines) == 5
        for i, line in enumerate(lines):
            data = json.loads(line)
            assert data["seq"] == i + 1
            assert set(data) == {
                "seq", "timestamp", "actor", "action",
                "surface", "payload", "prev_hash", "entry_hash",
            }


# ---------------------------------------------------------------------------
# Hashing internals
# ---------------------------------------------------------------------------


class TestHashing:
    def test_genesis_prev_hash(self, tmp_path):
        log = _make_log(tmp_path)
        entry = log.append("policy_engine", "validate_patch")
        assert entry.prev_hash == GENESIS_HASH

    def test_entry_hash_deterministic_across_fresh_logs(self, tmp_path):
        ts = datetime(2024, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        log_a = AuditLog(tmp_path / "a.jsonl")
        log_b = AuditLog(tmp_path / "b.jsonl")
        ea = log_a.append("reflex", "escalation", payload={"k": "v"}, timestamp=ts)
        eb = log_b.append("reflex", "escalation", payload={"k": "v"}, timestamp=ts)
        assert ea.entry_hash == eb.entry_hash
        # And the chains stay identical for a second entry
        ea2 = log_a.append("reflex", "escalation", payload={"k": "w"}, timestamp=ts)
        eb2 = log_b.append("reflex", "escalation", payload={"k": "w"}, timestamp=ts)
        assert ea2.entry_hash == eb2.entry_hash

    def test_canonical_excludes_entry_hash(self, tmp_path):
        entry = AuditEntry(
            seq=1,
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            actor="a",
            action="b",
            entry_hash="f" * 64,
        )
        canonical = entry.canonical()
        assert "entry_hash" not in canonical
        assert "f" * 64 not in canonical
        # Canonical form is deterministic regardless of payload key order
        e1 = AuditEntry(1, datetime(2024, 1, 1, tzinfo=timezone.utc),
                        "a", "b", payload={"x": 1, "y": 2})
        e2 = AuditEntry(1, datetime(2024, 1, 1, tzinfo=timezone.utc),
                        "a", "b", payload={"y": 2, "x": 1})
        assert e1.canonical() == e2.canonical()

    def test_compute_hash_matches_manual_sha256(self):
        entry = AuditEntry(
            seq=3,
            timestamp=datetime(2024, 2, 2, 3, 4, 5, tzinfo=timezone.utc),
            actor="promotion_pipeline",
            action="transition",
            payload={"patch_id": "p1"},
            prev_hash="a" * 64,
        )
        expected = hashlib.sha256(
            ("a" * 64 + entry.canonical()).encode("utf-8")
        ).hexdigest()
        assert entry.compute_hash() == expected

    def test_changing_any_field_changes_hash(self):
        base = dict(
            seq=1,
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            actor="a",
            action="b",
            surface="s",
            payload={"k": "v"},
        )
        h0 = AuditEntry(**base).compute_hash()
        for override in (
            {"seq": 2}, {"actor": "x"}, {"action": "y"},
            {"surface": "t"}, {"payload": {"k": "w"}},
            {"timestamp": datetime(2024, 1, 2, tzinfo=timezone.utc)},
        ):
            mutated = AuditEntry(**{**base, **override})
            assert mutated.compute_hash() != h0


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------


class TestTamperDetection:
    def test_flip_byte_in_line_3(self, tmp_path):
        log = _make_log(tmp_path)
        _populate(log, 10)
        path = tmp_path / "audit.jsonl"
        lines = _read_lines(path)
        # Flip a byte inside the payload message of line 3 (keep valid JSON)
        assert '"m0002"' in lines[2]
        lines[2] = lines[2].replace('"m0002"', '"mX002"')
        _write_lines(path, lines)
        result = log.verify_chain()
        assert not result.ok
        assert result.first_failure_seq == 3
        assert result.failure_reason in ("hash_mismatch", "link_broken")
        assert result.entries_checked == 2

    def test_delete_middle_line_detected(self, tmp_path):
        log = _make_log(tmp_path)
        _populate(log, 6)
        path = tmp_path / "audit.jsonl"
        lines = _read_lines(path)
        del lines[2]  # remove seq 3
        _write_lines(path, lines)
        result = log.verify_chain()
        assert not result.ok
        assert result.failure_reason in ("seq_gap", "link_broken")
        assert result.entries_checked == 2

    def test_swap_two_lines_detected(self, tmp_path):
        log = _make_log(tmp_path)
        _populate(log, 6)
        path = tmp_path / "audit.jsonl"
        lines = _read_lines(path)
        lines[2], lines[3] = lines[3], lines[2]
        _write_lines(path, lines)
        result = log.verify_chain()
        assert not result.ok
        assert result.failure_reason in ("reordered", "link_broken")

    def test_mid_file_corruption_fails_verify(self, tmp_path):
        log = _make_log(tmp_path)
        _populate(log, 5)
        path = tmp_path / "audit.jsonl"
        lines = _read_lines(path)
        lines[1] = "{not valid json at all"
        _write_lines(path, lines)
        result = log.verify_chain()
        assert not result.ok
        assert result.first_failure_seq == 2
        assert result.entries_checked == 1

    def test_forged_prev_hash_detected(self, tmp_path):
        log = _make_log(tmp_path)
        _populate(log, 4)
        path = tmp_path / "audit.jsonl"
        lines = _read_lines(path)
        data = json.loads(lines[1])
        data["prev_hash"] = "deadbeef" * 8
        lines[1] = json.dumps(data, sort_keys=True)
        _write_lines(path, lines)
        result = log.verify_chain()
        assert not result.ok
        assert result.failure_reason in ("link_broken", "hash_mismatch")

    def test_truncate_file_prefix_still_valid(self, tmp_path):
        log = _make_log(tmp_path)
        _populate(log, 10)
        path = tmp_path / "audit.jsonl"
        lines = _read_lines(path)
        _write_lines(path, lines[:3])
        result = log.verify_chain()
        assert result.ok
        assert result.entries_checked == 3
        assert log.verify_tail(100)
        assert log.verify_tail(2)

    def test_verify_tail_detects_recent_tamper(self, tmp_path):
        log = _make_log(tmp_path)
        _populate(log, 8)
        path = tmp_path / "audit.jsonl"
        lines = _read_lines(path)
        lines[6] = lines[6].replace('"m0006"', '"mY006"')
        _write_lines(path, lines)
        assert not log.verify_tail(3)
        # A window that excludes the tampered entry still verifies.
        assert log.verify_tail(1)

    def test_verify_tail_rejects_non_positive_n(self, tmp_path):
        log = _make_log(tmp_path)
        with pytest.raises(ValueError):
            log.verify_tail(0)


# ---------------------------------------------------------------------------
# Reopen / resume / quarantine
# ---------------------------------------------------------------------------


class TestReopen:
    def test_reopen_resumes_seq_and_prev_hash(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        log = AuditLog(path)
        entries = _populate(log, 5)
        reopened = AuditLog(path)
        assert len(reopened) == 5
        last = reopened.last()
        assert last is not None
        assert last.seq == 5
        assert last.entry_hash == entries[-1].entry_hash
        new_entry = reopened.append("tester", "validate_patch")
        assert new_entry.seq == 6
        assert new_entry.prev_hash == entries[-1].entry_hash

    def test_append_after_reopen_keeps_chain_valid(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        log = AuditLog(path)
        _populate(log, 4)
        reopened = AuditLog(path)
        _populate(reopened, 6)
        assert len(reopened) == 10
        result = reopened.verify_chain()
        assert result.ok and result.entries_checked == 10
        # A third open sees the full chain
        third = AuditLog(path)
        assert len(third) == 10
        assert third.verify_chain().ok

    def test_partial_last_line_quarantined_on_open(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        log = AuditLog(path)
        _populate(log, 5)
        # Simulate a torn write: garbage bytes appended without newline
        with path.open("a", encoding="utf-8") as fh:
            fh.write('{"seq": 6, "timestamp": "2024-01-01T00:00:0')
        reopened = AuditLog(path)
        assert len(reopened) == 5
        assert reopened.last() is not None and reopened.last().seq == 5
        result = reopened.verify_chain()
        assert result.ok and result.entries_checked == 5

    def test_append_after_quarantine_keeps_chain_valid(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        log = AuditLog(path)
        _populate(log, 3)
        with path.open("a", encoding="utf-8") as fh:
            fh.write('{"seq": 4, BROKEN')
        reopened = AuditLog(path)
        reopened.append("tester", "validate_patch")
        assert len(reopened) == 4
        assert reopened.verify_chain().ok
        # Quarantined bytes were truncated from the file
        assert len(_read_lines(path)) == 4

    def test_verify_chain_tolerates_corrupt_tail(self, tmp_path):
        # Even without reopening (no quarantine truncation), verify_chain
        # treats a corrupt final line as a partial write.
        log = _make_log(tmp_path)
        _populate(log, 4)
        path = tmp_path / "audit.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write("TAIL-GARBAGE")
        result = log.verify_chain()
        assert result.ok and result.entries_checked == 4

    def test_parent_directories_created(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "audit.jsonl"
        log = AuditLog(deep)
        log.append("tester", "validate_patch")
        assert deep.exists()
        assert log.verify_chain().ok


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


@pytest.fixture
def mixed_log(tmp_path):
    """Log with a mix of actors/actions/surfaces and known timestamps."""
    log = AuditLog(tmp_path / "audit.jsonl")
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    log.append("policy_engine", "validate_patch", surface="tools",
               payload={"allowed": True}, timestamp=base)
    log.append("policy_engine", "validate_patch", surface="sandbox",
               payload={"allowed": False}, timestamp=base + timedelta(hours=1))
    log.append("promotion_pipeline", "transition",
               payload={"patch_id": "p1"}, timestamp=base + timedelta(hours=2))
    log.append("reflex", "escalation", surface="tools",
               payload={"rule": "r1"}, timestamp=base + timedelta(hours=3))
    log.append("reflex", "rubric_update",
               payload={"rubric_id": "rb1"}, timestamp=base + timedelta(hours=4))
    return log, base


class TestQuery:
    def test_no_filters_returns_all(self, mixed_log):
        log, _ = mixed_log
        assert len(log.query()) == 5

    def test_filter_actor(self, mixed_log):
        log, _ = mixed_log
        results = log.query(actor="reflex")
        assert len(results) == 2
        assert all(e.actor == "reflex" for e in results)

    def test_filter_action(self, mixed_log):
        log, _ = mixed_log
        results = log.query(action="validate_patch")
        assert len(results) == 2
        assert all(e.action == "validate_patch" for e in results)

    def test_filter_surface(self, mixed_log):
        log, _ = mixed_log
        results = log.query(surface="tools")
        assert len(results) == 2
        assert all(e.surface == "tools" for e in results)

    def test_filter_since_datetime(self, mixed_log):
        log, base = mixed_log
        results = log.query(since=base + timedelta(hours=2))
        assert [e.seq for e in results] == [3, 4, 5]

    def test_filter_since_iso_string(self, mixed_log):
        log, base = mixed_log
        since = (base + timedelta(hours=2)).isoformat()
        results = log.query(since=since)
        assert [e.seq for e in results] == [3, 4, 5]

    def test_filters_compose(self, mixed_log):
        log, base = mixed_log
        results = log.query(
            actor="reflex", surface="tools", since=base + timedelta(minutes=30)
        )
        assert len(results) == 1
        assert results[0].action == "escalation"
        # Impossible combination -> empty
        assert log.query(actor="reflex", action="transition") == []

    def test_query_preserves_chain_order(self, mixed_log):
        log, _ = mixed_log
        seqs = [e.seq for e in log.query()]
        assert seqs == sorted(seqs)


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_concurrent_appends_30x10(self, tmp_path):
        log = _make_log(tmp_path)
        barrier = threading.Barrier(30)
        errors: list = []

        def worker(tid: int) -> None:
            try:
                barrier.wait(timeout=30)
                for i in range(10):
                    log.append("tester", "validate_patch",
                               payload={"tid": tid, "i": i})
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert not errors
        assert len(log) == 300
        seqs = sorted(e.seq for e in log.query())
        assert seqs == list(range(1, 301))
        result = log.verify_chain()
        assert result.ok and result.entries_checked == 300


# ---------------------------------------------------------------------------
# AuditRecorder facade
# ---------------------------------------------------------------------------


class TestAuditRecorder:
    def test_record_policy_decision(self, tmp_path):
        log = _make_log(tmp_path)
        rec = AuditRecorder(log)
        entry = rec.record_policy_decision("tools", True, ["within_scope"])
        assert entry is not None
        assert entry.actor == "policy_engine"
        assert entry.action == "validate_patch"
        assert entry.surface == "tools"
        assert entry.payload == {"allowed": True, "reasons": ["within_scope"]}

    def test_record_transition(self, tmp_path):
        log = _make_log(tmp_path)
        rec = AuditRecorder(log)
        entry = rec.record_transition("patch-7", "proposed", "evaluated")
        assert entry is not None
        assert entry.actor == "promotion_pipeline"
        assert entry.action == "transition"
        assert entry.payload == {
            "patch_id": "patch-7",
            "from_state": "proposed",
            "to_state": "evaluated",
        }

    def test_record_reflex_decision(self, tmp_path):
        log = _make_log(tmp_path)
        rec = AuditRecorder(log)
        entry = rec.record_reflex_decision("score_gate", 7.5, 0.4, True)
        assert entry is not None
        assert entry.actor == "reflex"
        assert entry.action == "escalation"
        assert entry.payload == {
            "rule": "score_gate", "value": 7.5,
            "confidence": 0.4, "escalate": True,
        }

    def test_record_rubric_update(self, tmp_path):
        log = _make_log(tmp_path)
        rec = AuditRecorder(log)
        entry = rec.record_rubric_update("rubric-1", "v3", "v4", patch_hash="abc123")
        assert entry is not None
        assert entry.actor == "reflex"
        assert entry.action == "rubric_update"
        assert entry.payload == {
            "rubric_id": "rubric-1", "parent_version": "v3",
            "new_version": "v4", "patch_hash": "abc123",
        }

    def test_recorder_writes_valid_chain(self, tmp_path):
        log = _make_log(tmp_path)
        rec = AuditRecorder(log)
        rec.record_policy_decision("tools", False, ["deny_rule"])
        rec.record_transition("p1", "proposed", "quarantined")
        rec.record_reflex_decision("conf", 0.2, 0.2, True)
        rec.record_rubric_update("rb", "v1", "v2")
        assert len(log) == 4
        assert log.verify_chain().ok

    def test_none_log_is_noop(self):
        rec = AuditRecorder(None)
        assert rec.log is None
        assert rec.record_policy_decision("tools", True, []) is None
        assert rec.record_transition("p", "a", "b") is None
        assert rec.record_reflex_decision("r", 1.0, 0.5, False) is None
        assert rec.record_rubric_update("rb", "v1", "v2") is None


# ---------------------------------------------------------------------------
# Payload round-trips
# ---------------------------------------------------------------------------


class TestPayloadRoundTrip:
    def test_nested_dicts_unicode_datetime(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        log = AuditLog(path)
        when = datetime(2024, 3, 14, 15, 9, 26, tzinfo=timezone.utc)
        payload = {
            "nested": {"a": [1, 2, {"b": None}], "深": "值"},
            "unicode": "héllo wörld ✓",
            "when": when,
            "number": 3.5,
        }
        log.append("reflex", "escalation", payload=payload)
        # Read back from disk through a fresh log object
        reopened = AuditLog(path)
        entry = reopened.last()
        assert entry is not None
        assert entry.payload["nested"] == {"a": [1, 2, {"b": None}], "深": "值"}
        assert entry.payload["unicode"] == "héllo wörld ✓"
        assert entry.payload["when"] == str(when)  # default=str on write
        assert entry.payload["number"] == 3.5
        assert reopened.verify_chain().ok

    def test_unicode_roundtrip_preserves_hash(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        log = AuditLog(path)
        entry = log.append("policy_engine", "validate_patch",
                           payload={"msg": "üñïçødé"})
        raw = json.loads(_read_lines(path)[0])
        assert raw["entry_hash"] == entry.entry_hash
        assert AuditEntry.from_dict(raw).compute_hash() == entry.entry_hash

    def test_empty_and_default_fields(self, tmp_path):
        log = _make_log(tmp_path)
        entry = log.append("a", "b")
        assert entry.surface == ""
        assert entry.payload == {}
        reopened = AuditLog(tmp_path / "audit.jsonl")
        last = reopened.last()
        assert last is not None
        assert last.surface == "" and last.payload == {}

    def test_naive_timestamp_treated_as_utc(self, tmp_path):
        log = _make_log(tmp_path)
        naive = datetime(2024, 6, 1, 10, 0, 0)
        entry = log.append("a", "b", timestamp=naive)
        assert entry.timestamp.tzinfo is not None
        assert log.verify_chain().ok
