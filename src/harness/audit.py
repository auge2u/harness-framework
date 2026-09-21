"""Immutable, hash-chained audit log for the Harness framework.

Compliance requires tamper-evident audit trails.  This module provides an
append-only JSONL log where every entry carries the SHA-256 of its
predecessor; any modification, deletion, or reordering of recorded entries
breaks the chain and is detectable by :meth:`AuditLog.verify_chain`.

The log records policy decisions, patch evaluations, promotion transitions,
reflex decisions, and rubric lineage updates (see ``REFLEX_PLAN.md``).  The
:class:`AuditRecorder` facade gives other subsystems a thin, None-safe
integration point without requiring signature changes in those subsystems.

Zero-dependency: only the Python standard library is used.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

__all__ = [
    "GENESIS_HASH",
    "AuditEntry",
    "AuditLog",
    "AuditRecorder",
    "ChainVerification",
]

logger = logging.getLogger(__name__)

#: Hash assigned to the ``prev_hash`` field of the first entry in a chain.
GENESIS_HASH = "0" * 64

#: Failure reasons reported by :class:`ChainVerification`.
REASON_HASH_MISMATCH = "hash_mismatch"
REASON_SEQ_GAP = "seq_gap"
REASON_LINK_BROKEN = "link_broken"
REASON_REORDERED = "reordered"


# ---------------------------------------------------------------------------
# AuditEntry
# ---------------------------------------------------------------------------


@dataclass
class AuditEntry:
    """One chained audit record.

    Attributes
    ----------
    seq:
        Monotonic sequence number, starting at 1 for the first entry.
    timestamp:
        When the entry was recorded (UTC-aware).
    actor:
        Subsystem that produced the record, e.g. ``"policy_engine"``,
        ``"promotion_pipeline"``, or ``"reflex"``.
    action:
        What happened, e.g. ``"validate_patch"``, ``"transition"``,
        ``"escalation"``, or ``"rubric_update"``.
    surface:
        Optional surface the action applies to.
    payload:
        Arbitrary structured detail.  Serialised with ``default=str`` so
        nested dicts, unicode, and datetimes round-trip.
    prev_hash:
        SHA-256 hex digest of the previous entry, or :data:`GENESIS_HASH`
        for the first entry.
    entry_hash:
        SHA-256 of ``prev_hash + canonical()``; computed on append.
    """

    seq: int
    timestamp: datetime
    actor: str
    action: str
    surface: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    prev_hash: str = GENESIS_HASH
    entry_hash: str = ""

    def __post_init__(self) -> None:
        """Normalise field types after construction."""
        self.seq = int(self.seq)
        if isinstance(self.timestamp, str):
            self.timestamp = _parse_timestamp(self.timestamp)
        if self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)
        self.actor = str(self.actor)
        self.action = str(self.action)
        self.surface = "" if self.surface is None else str(self.surface)
        if self.payload is None:
            self.payload = {}
        self.prev_hash = str(self.prev_hash)
        self.entry_hash = str(self.entry_hash)

    def canonical(self) -> str:
        """Return the canonical JSON form of the content fields.

        Covers every field except :attr:`entry_hash`, with keys sorted and
        compact separators so the serialisation is deterministic across
        processes and platforms.
        """
        content = {
            "seq": self.seq,
            "timestamp": self.timestamp.isoformat(),
            "actor": self.actor,
            "action": self.action,
            "surface": self.surface,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
        }
        return json.dumps(
            content,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        )

    def compute_hash(self) -> str:
        """Return ``sha256(prev_hash + canonical())`` as a hex digest."""
        material = (self.prev_hash + self.canonical()).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dictionary of all fields."""
        return {
            "seq": self.seq,
            "timestamp": self.timestamp.isoformat(),
            "actor": self.actor,
            "action": self.action,
            "surface": self.surface,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AuditEntry":
        """Rebuild an entry from its dictionary form.

        Raises
        ------
        ValueError
            If required fields are missing or have invalid types.
        """
        if not isinstance(data, Mapping):
            raise ValueError("audit entry must be a JSON object")
        try:
            seq = data["seq"]
            timestamp = data["timestamp"]
            actor = data["actor"]
            action = data["action"]
            prev_hash = data["prev_hash"]
            entry_hash = data["entry_hash"]
        except KeyError as exc:
            raise ValueError(f"audit entry missing field: {exc}") from exc
        if isinstance(seq, bool) or not isinstance(seq, int):
            raise ValueError("audit entry 'seq' must be an integer")
        if not isinstance(timestamp, (str, datetime)):
            raise ValueError("audit entry 'timestamp' must be an ISO string")
        for name, value in (("actor", actor), ("action", action),
                            ("prev_hash", prev_hash), ("entry_hash", entry_hash)):
            if not isinstance(value, str):
                raise ValueError(f"audit entry {name!r} must be a string")
        surface = data.get("surface", "")
        payload = data.get("payload", {})
        if not isinstance(surface, str):
            raise ValueError("audit entry 'surface' must be a string")
        if not isinstance(payload, dict):
            raise ValueError("audit entry 'payload' must be an object")
        return cls(
            seq=seq,
            timestamp=timestamp,
            actor=actor,
            action=action,
            surface=surface,
            payload=payload,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
        )


# ---------------------------------------------------------------------------
# ChainVerification
# ---------------------------------------------------------------------------


@dataclass
class ChainVerification:
    """Result of replaying and validating an audit chain.

    Attributes
    ----------
    ok:
        Whether the entire chain verified.
    entries_checked:
        Number of entries that verified successfully before any failure.
    first_failure_seq:
        Sequence number at which the first failure was detected, or
        ``None`` when the chain is intact.
    failure_reason:
        One of ``"hash_mismatch"``, ``"seq_gap"``, ``"link_broken"``,
        or ``"reordered"``; empty when the chain is intact.
    """

    ok: bool
    entries_checked: int
    first_failure_seq: Optional[int] = None
    failure_reason: str = ""

    def __bool__(self) -> bool:
        """Return :attr:`ok` so the result can be used in boolean context."""
        return self.ok


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------


class AuditLog:
    """Append-only, hash-chained, tamper-evident audit log (JSONL file).

    Each call to :meth:`append` writes exactly one JSON line carrying the
    SHA-256 of its predecessor and fsyncs the file.  Reopening an existing
    log resumes ``seq``/``prev_hash`` from its last valid line.

    A corrupt final line (e.g. from a partial write during a crash) is
    quarantined on open: it is logged and truncated from the file so the
    chain can continue.  Corruption anywhere else in the file is left in
    place and causes :meth:`verify_chain` to fail.

    All mutating operations are thread-safe.
    """

    def __init__(self, path: Union[str, Path]) -> None:
        """Open (or create) the audit log at *path*.

        Parameters
        ----------
        path:
            Filesystem path of the JSONL log file.  Parent directories are
            created as needed.
        """
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._last: Optional[AuditEntry] = None
        self._count = 0
        self._load_state()

    # -- Internal helpers ----------------------------------------------------

    def _load_state(self) -> None:
        """Resume sequence/hash state from an existing log file.

        Parses every line; the last valid entry becomes the resume point.
        A corrupt trailing line is quarantined (logged + truncated).
        Mid-file corrupt lines are left untouched for
        :meth:`verify_chain` to report.
        """
        if not self._path.exists():
            return
        data = self._path.read_bytes()
        if not data:
            return
        raw_lines = data.split(b"\n")
        offset = 0
        last_good_end = 0
        truncate_at: Optional[int] = None
        for index, raw in enumerate(raw_lines):
            line_end = offset + len(raw) + 1  # account for the '\n'
            stripped = raw.strip()
            if stripped:
                try:
                    entry = AuditEntry.from_dict(json.loads(stripped.decode("utf-8")))
                except (ValueError, UnicodeDecodeError):
                    is_tail = all(
                        not later.strip() for later in raw_lines[index + 1:]
                    )
                    if is_tail:
                        logger.warning(
                            "AuditLog: quarantining corrupt final line of %s "
                            "(%d bytes): %r",
                            self._path, len(stripped), stripped[:80],
                        )
                        truncate_at = last_good_end
                    else:
                        logger.warning(
                            "AuditLog: corrupt mid-file line %d in %s; "
                            "verify_chain will report it",
                            index + 1, self._path,
                        )
                else:
                    self._last = entry
                    self._count += 1
                    last_good_end = line_end
            offset = line_end
        if truncate_at is not None:
            try:
                with self._path.open("r+b") as fh:
                    fh.truncate(truncate_at)
            except OSError:
                logger.exception(
                    "AuditLog: failed to truncate quarantined tail of %s",
                    self._path,
                )

    def _read_raw_lines(self) -> List[str]:
        """Return all non-empty raw lines currently in the log file."""
        if not self._path.exists():
            return []
        text = self._path.read_text(encoding="utf-8")
        return [line for line in text.splitlines() if line.strip()]

    @staticmethod
    def _parse_line(line: str) -> AuditEntry:
        """Parse one raw JSONL line into an :class:`AuditEntry`.

        Raises :class:`ValueError` if the line is not a valid entry.
        """
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc}") from exc
        return AuditEntry.from_dict(data)

    @staticmethod
    def _check_entry(
        entry: AuditEntry,
        expected_seq: int,
        prev_hash: str,
        checked: int,
    ) -> Optional[ChainVerification]:
        """Validate one entry against the running chain state.

        Returns a failing :class:`ChainVerification`, or ``None`` when the
        entry is consistent with the chain so far.
        """
        if entry.prev_hash != prev_hash:
            return ChainVerification(
                False, checked, entry.seq, REASON_LINK_BROKEN
            )
        if entry.seq < expected_seq:
            return ChainVerification(
                False, checked, entry.seq, REASON_REORDERED
            )
        if entry.seq > expected_seq:
            return ChainVerification(
                False, checked, entry.seq, REASON_SEQ_GAP
            )
        if entry.compute_hash() != entry.entry_hash:
            return ChainVerification(
                False, checked, entry.seq, REASON_HASH_MISMATCH
            )
        return None

    # -- Public API ----------------------------------------------------------

    def append(
        self,
        actor: str,
        action: str,
        surface: str = "",
        payload: Optional[Dict[str, Any]] = None,
        *,
        timestamp: Optional[datetime] = None,
    ) -> AuditEntry:
        """Append a new entry to the chain.

        Parameters
        ----------
        actor, action, surface, payload:
            See :class:`AuditEntry`.
        timestamp:
            Optional explicit timestamp (defaults to ``now(UTC)``).  Useful
            for deterministic hashing in tests and replays.

        Returns
        -------
        AuditEntry
            The appended entry, with :attr:`AuditEntry.entry_hash` computed.
        """
        with self._lock:
            prev = self._last
            entry = AuditEntry(
                seq=(prev.seq + 1) if prev is not None else 1,
                timestamp=timestamp or datetime.now(timezone.utc),
                actor=actor,
                action=action,
                surface=surface,
                payload=dict(payload) if payload else {},
                prev_hash=prev.entry_hash if prev is not None else GENESIS_HASH,
            )
            entry.entry_hash = entry.compute_hash()
            line = json.dumps(
                entry.to_dict(),
                sort_keys=True,
                ensure_ascii=True,
                default=str,
            )
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            self._last = entry
            self._count += 1
            return entry

    def verify_chain(self) -> ChainVerification:
        """Replay the whole file and validate the chain.

        Checks, in order per entry: ``prev_hash`` linkage, sequence
        continuity (gaps and reordering), and recomputation of
        ``entry_hash``.  A corrupt trailing line (partial write) is skipped
        as quarantined; mid-file corruption fails the verification.

        Returns
        -------
        ChainVerification
            ``ok=True`` when every present entry verifies.  Truncation of
            the file is *not* a failure — the surviving prefix still forms
            a valid chain.
        """
        with self._lock:
            lines = self._read_raw_lines()
        expected_seq = 1
        prev_hash = GENESIS_HASH
        checked = 0
        for index, line in enumerate(lines):
            try:
                entry = self._parse_line(line)
            except ValueError:
                if index == len(lines) - 1:
                    # Quarantined partial write at the tail — ignore.
                    break
                return ChainVerification(
                    False, checked, expected_seq, REASON_HASH_MISMATCH
                )
            failure = self._check_entry(entry, expected_seq, prev_hash, checked)
            if failure is not None:
                return failure
            prev_hash = entry.entry_hash
            expected_seq += 1
            checked += 1
        return ChainVerification(True, checked)

    def verify_tail(self, n: int = 100) -> bool:
        """Fast-path verification of the last *n* entries.

        Validates sequence continuity, hash linkage, and entry-hash
        recomputation within the trailing window.  When the window reaches
        the start of the log, genesis linkage is verified as well.  A
        corrupt trailing line is skipped (quarantined partial write); any
        other corruption in the window fails the check.

        Returns
        -------
        bool
            ``True`` when the window verifies (also for an empty log).
        """
        if n <= 0:
            raise ValueError("n must be a positive integer")
        with self._lock:
            lines = self._read_raw_lines()
        entries: List[AuditEntry] = []
        for index, line in enumerate(lines):
            try:
                entries.append(self._parse_line(line))
            except ValueError:
                if index == len(lines) - 1:
                    continue  # quarantined tail
                return False
        window = entries[-n:]
        if not window:
            return True
        for i, entry in enumerate(window):
            if i == 0:
                if entry.seq == 1 and entry.prev_hash != GENESIS_HASH:
                    return False
                if entry.compute_hash() != entry.entry_hash:
                    return False
                continue
            prev = window[i - 1]
            if entry.seq != prev.seq + 1:
                return False
            if entry.prev_hash != prev.entry_hash:
                return False
            if entry.compute_hash() != entry.entry_hash:
                return False
        return True

    def query(
        self,
        actor: Optional[str] = None,
        action: Optional[str] = None,
        surface: Optional[str] = None,
        since: Optional[Union[str, datetime]] = None,
    ) -> List[AuditEntry]:
        """Return entries matching all supplied filters (AND-combined).

        Parameters
        ----------
        actor, action, surface:
            Exact-match filters; ``None`` disables the filter.
        since:
            Optional lower bound on the entry timestamp, as a
            :class:`~datetime.datetime` or ISO-8601 string.  Naive
            datetimes are interpreted as UTC.

        Returns
        -------
        list[AuditEntry]
            Matching entries in chain (file) order.  Unparseable lines are
            skipped — use :meth:`verify_chain` for integrity checks.
        """
        since_dt: Optional[datetime] = None
        if since is not None:
            since_dt = _parse_timestamp(since) if isinstance(since, str) else since
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
        with self._lock:
            lines = self._read_raw_lines()
        results: List[AuditEntry] = []
        for line in lines:
            try:
                entry = self._parse_line(line)
            except ValueError:
                continue
            if actor is not None and entry.actor != actor:
                continue
            if action is not None and entry.action != action:
                continue
            if surface is not None and entry.surface != surface:
                continue
            if since_dt is not None and entry.timestamp < since_dt:
                continue
            results.append(entry)
        return results

    def last(self) -> Optional[AuditEntry]:
        """Return the most recent valid entry, or ``None`` for an empty log."""
        with self._lock:
            return self._last

    def __len__(self) -> int:
        """Return the number of valid entries in the log."""
        with self._lock:
            return self._count

    def __repr__(self) -> str:
        return f"AuditLog(path={str(self._path)!r}, entries={self._count})"


# ---------------------------------------------------------------------------
# AuditRecorder — None-safe facade for subsystem integration
# ---------------------------------------------------------------------------


class AuditRecorder:
    """Thin, None-safe facade other subsystems can hold optionally.

    Each ``record_*`` method appends a semantically shaped entry to the
    underlying :class:`AuditLog`; when the log is ``None`` every method is
    a no-op returning ``None`` so callers never need to branch.
    """

    def __init__(self, log: Optional[AuditLog]) -> None:
        """Store the (possibly ``None``) audit log."""
        self._log = log

    @property
    def log(self) -> Optional[AuditLog]:
        """Return the underlying log (may be ``None``)."""
        return self._log

    def record_policy_decision(
        self,
        surface: str,
        allowed: bool,
        reasons: List[str],
    ) -> Optional[AuditEntry]:
        """Record a policy-engine decision on a surface.

        Writes actor ``"policy_engine"``, action ``"validate_patch"``.
        No-op (returns ``None``) when no log is attached.
        """
        if self._log is None:
            return None
        return self._log.append(
            actor="policy_engine",
            action="validate_patch",
            surface=surface,
            payload={"allowed": bool(allowed), "reasons": list(reasons)},
        )

    def record_transition(
        self,
        patch_id: str,
        from_state: str,
        to_state: str,
    ) -> Optional[AuditEntry]:
        """Record a promotion-pipeline state transition.

        Writes actor ``"promotion_pipeline"``, action ``"transition"``.
        No-op (returns ``None``) when no log is attached.
        """
        if self._log is None:
            return None
        return self._log.append(
            actor="promotion_pipeline",
            action="transition",
            payload={
                "patch_id": str(patch_id),
                "from_state": str(from_state),
                "to_state": str(to_state),
            },
        )

    def record_reflex_decision(
        self,
        rule: str,
        value: float,
        confidence: float,
        escalate: bool,
    ) -> Optional[AuditEntry]:
        """Record a reflex-tier decision (System 1 → System 2 handoff).

        Writes actor ``"reflex"``, action ``"escalation"``.
        No-op (returns ``None``) when no log is attached.
        """
        if self._log is None:
            return None
        return self._log.append(
            actor="reflex",
            action="escalation",
            payload={
                "rule": str(rule),
                "value": float(value),
                "confidence": float(confidence),
                "escalate": bool(escalate),
            },
        )

    def record_rubric_update(
        self,
        rubric_id: str,
        parent_version: str,
        new_version: str,
        patch_hash: str = "",
    ) -> Optional[AuditEntry]:
        """Record rubric lineage: a rubric version derived from a parent.

        Writes actor ``"reflex"``, action ``"rubric_update"``.
        No-op (returns ``None``) when no log is attached.
        """
        if self._log is None:
            return None
        return self._log.append(
            actor="reflex",
            action="rubric_update",
            payload={
                "rubric_id": str(rubric_id),
                "parent_version": str(parent_version),
                "new_version": str(new_version),
                "patch_hash": str(patch_hash),
            },
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_timestamp(value: Union[str, datetime]) -> datetime:
    """Parse an ISO-8601 timestamp string into a datetime.

    Raises :class:`ValueError` for malformed input.
    """
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError(f"timestamp must be str or datetime, got {type(value).__name__}")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid ISO timestamp: {value!r}") from exc
