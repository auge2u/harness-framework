"""Content-addressed snapshot store for Harness artifacts.

A :class:`ContentAddressedStore` persists arbitrary JSON-serialisable
artifacts (``HarnessConfig`` dicts, reflex ``Rubric`` payloads, or any other
mapping/dataclass) exactly once, keyed by the SHA-256 of their canonical
JSON serialisation.  Identical content is deduplicated automatically —
storing the same artifact twice returns the existing reference without
rewriting the blob.

Layout on disk::

    <root>/
        index.jsonl            # append-only sidecar index (one JSON object per line)
        ab/                    # first two hex chars of the content hash (sharding)
            abcdef....json     # the canonical-JSON blob, named by full hash

The store is generic: it does not interpret artifact content, only the
``artifact_type`` tag (``"config"``, ``"rubric"``, or any caller-chosen
string) recorded alongside each entry in the index.

Stdlib only; thread-safe for concurrent :meth:`ContentAddressedStore.put`
calls via an internal re-entrant lock and atomic blob writes.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

__all__ = ["ContentAddressedStore", "SnapshotNotFoundError", "SnapshotRef"]


class SnapshotNotFoundError(Exception):
    """Raised when a requested content hash is not present in the store."""


@dataclass
class SnapshotRef:
    """Pointer to a stored snapshot.

    Attributes
    ----------
    content_hash:
        SHA-256 hex digest of the canonical JSON serialisation.
    artifact_type:
        Caller-supplied type tag, e.g. ``"config"`` or ``"rubric"``.
    stored_at:
        UTC timestamp of when the artifact was (first) stored.
    path:
        Blob path relative to the store root, e.g. ``"ab/abcdef....json"``.
    size_bytes:
        Size of the stored blob in bytes.
    metadata:
        Free-form caller-supplied metadata recorded in the index.
    """

    content_hash: str
    artifact_type: str
    stored_at: datetime
    path: str
    size_bytes: int
    metadata: Dict[str, Any] = field(default_factory=dict)


def _to_plain_dict(artifact: Union[Mapping[str, Any], Any]) -> Dict[str, Any]:
    """Normalise *artifact* to a plain dictionary.

    Accepts mappings (copied), dataclass instances (via
    :func:`dataclasses.asdict`), and objects exposing ``__dict__``.
    """
    if isinstance(artifact, Mapping):
        return dict(artifact)
    if is_dataclass(artifact) and not isinstance(artifact, type):
        return asdict(artifact)
    if hasattr(artifact, "__dict__"):
        return dict(vars(artifact))
    raise TypeError(
        f"Unsupported artifact type {type(artifact).__name__!r}: "
        "expected a Mapping, dataclass instance, or object with __dict__"
    )


def _canonical_bytes(payload: Dict[str, Any]) -> bytes:
    """Serialise *payload* to canonical JSON bytes (sorted keys, compact)."""
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    )
    return canonical.encode("utf-8")


def _parse_ts(value: Any) -> datetime:
    """Parse an ISO-8601 timestamp back into a :class:`datetime`."""
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


class ContentAddressedStore:
    """File-tree content-addressed artifact store.

    Parameters
    ----------
    root:
        Directory under which blobs and the sidecar ``index.jsonl`` live,
        e.g. ``.harness_lineage/snapshots``.  Created if it does not exist.
    """

    _INDEX_FILE = "index.jsonl"

    def __init__(self, root: Union[str, Path]) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._index_path = self._root / self._INDEX_FILE
        self._index_path.touch(exist_ok=True)
        self._lock = threading.RLock()

    # -- Internal helpers -----------------------------------------------------

    def _blob_relpath(self, content_hash: str) -> str:
        """Return the root-relative blob path for *content_hash* (sharded)."""
        return f"{content_hash[:2]}/{content_hash}.json"

    def _blob_abspath(self, content_hash: str) -> Path:
        """Return the absolute blob path for *content_hash*."""
        return self._root / content_hash[:2] / f"{content_hash}.json"

    def _append_index(self, entry: Dict[str, Any]) -> None:
        """Append a single record to the sidecar ``index.jsonl``."""
        with self._index_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True, default=str) + "\n")

    def _read_index(self) -> List[Dict[str, Any]]:
        """Read every record from the sidecar index, oldest first."""
        entries: List[Dict[str, Any]] = []
        if not self._index_path.exists():
            return entries
        with self._index_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries

    def _live_entries(self) -> "Dict[str, Dict[str, Any]]":
        """Return the latest non-tombstone index entry per content hash."""
        live: Dict[str, Dict[str, Any]] = {}
        for entry in self._read_index():
            content_hash = entry.get("hash", "")
            if not content_hash:
                continue
            if entry.get("action") == "delete":
                live.pop(content_hash, None)
            else:
                live[content_hash] = entry
        return live

    # -- Public API -----------------------------------------------------------

    def put(
        self,
        artifact: Union[Mapping[str, Any], Any],
        artifact_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SnapshotRef:
        """Store *artifact* under its content hash, deduplicating on content.

        The artifact is serialised to canonical JSON (sorted keys, compact
        separators, ``default=str``) and hashed with SHA-256.  If a blob with
        the same hash already exists it is **not** rewritten; the existing
        reference is returned and the put is counted as a dedupe hit.

        Parameters
        ----------
        artifact:
            A mapping, dataclass instance, or object with ``__dict__``.
        artifact_type:
            Type tag recorded in the index (e.g. ``"config"``, ``"rubric"``).
        metadata:
            Optional free-form metadata recorded in the index.

        Returns
        -------
        SnapshotRef
            Pointer to the stored blob.
        """
        payload = _to_plain_dict(artifact)
        blob = _canonical_bytes(payload)
        content_hash = hashlib.sha256(blob).hexdigest()
        relpath = self._blob_relpath(content_hash)
        abspath = self._root / relpath
        now = datetime.now(timezone.utc)

        with self._lock:
            dedupe = abspath.exists()
            if not dedupe:
                abspath.parent.mkdir(parents=True, exist_ok=True)
                tmp = abspath.with_name(f".{abspath.name}.{threading.get_ident()}.tmp")
                tmp.write_bytes(blob)
                tmp.replace(abspath)
            self._append_index(
                {
                    "action": "put",
                    "hash": content_hash,
                    "artifact_type": artifact_type,
                    "stored_at": now.isoformat(),
                    "path": relpath,
                    "size_bytes": len(blob),
                    "dedupe": dedupe,
                    "metadata": dict(metadata) if metadata else {},
                }
            )

        return SnapshotRef(
            content_hash=content_hash,
            artifact_type=artifact_type,
            stored_at=now,
            path=relpath,
            size_bytes=len(blob),
            metadata=dict(metadata) if metadata else {},
        )

    def get(self, content_hash: str) -> Dict[str, Any]:
        """Load and return the artifact stored under *content_hash*.

        Raises
        ------
        SnapshotNotFoundError
            If no blob exists for *content_hash*.
        """
        abspath = self._blob_abspath(content_hash)
        if not abspath.exists():
            raise SnapshotNotFoundError(
                f"No snapshot stored for content hash {content_hash!r}"
            )
        with abspath.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise SnapshotNotFoundError(
                f"Snapshot {content_hash!r} does not contain a JSON object"
            )
        return data

    def has(self, content_hash: str) -> bool:
        """Return ``True`` if a blob exists for *content_hash*."""
        return self._blob_abspath(content_hash).exists()

    def get_typed(self, content_hash: str, artifact_type: str) -> Dict[str, Any]:
        """Load an artifact, validating its recorded type tag.

        Raises
        ------
        SnapshotNotFoundError
            If no blob exists for *content_hash*.
        TypeError
            If the type recorded in the index does not match *artifact_type*.
        """
        artifact = self.get(content_hash)
        live = self._live_entries()
        entry = live.get(content_hash)
        recorded = entry.get("artifact_type") if entry else None
        if recorded is not None and recorded != artifact_type:
            raise TypeError(
                f"Snapshot {content_hash!r} has artifact type {recorded!r}, "
                f"not {artifact_type!r}"
            )
        return artifact

    def list(self, artifact_type: Optional[str] = None) -> List[SnapshotRef]:
        """List live snapshots, newest last.

        Parameters
        ----------
        artifact_type:
            If given, only snapshots recorded with this type tag are
            returned.  Tombstoned (deleted) hashes are always excluded.
        """
        refs: List[SnapshotRef] = []
        for content_hash, entry in self._live_entries().items():
            if artifact_type is not None and entry.get("artifact_type") != artifact_type:
                continue
            refs.append(
                SnapshotRef(
                    content_hash=content_hash,
                    artifact_type=str(entry.get("artifact_type", "")),
                    stored_at=_parse_ts(entry.get("stored_at")),
                    path=str(entry.get("path", self._blob_relpath(content_hash))),
                    size_bytes=int(entry.get("size_bytes", 0)),
                    metadata=dict(entry.get("metadata") or {}),
                )
            )
        return refs

    def delete(self, content_hash: str) -> bool:
        """Remove the blob for *content_hash*, leaving an index tombstone.

        Returns ``True`` if a blob was removed, ``False`` if none existed.
        """
        with self._lock:
            abspath = self._blob_abspath(content_hash)
            removed = abspath.exists()
            if removed:
                abspath.unlink()
            self._append_index(
                {
                    "action": "delete",
                    "hash": content_hash,
                    "stored_at": datetime.now(timezone.utc).isoformat(),
                    "path": self._blob_relpath(content_hash),
                    "removed": removed,
                }
            )
        return removed

    def verify(self, content_hash: str) -> bool:
        """Re-hash the stored blob and compare against *content_hash*.

        Returns ``True`` only if the blob exists and its SHA-256 matches,
        i.e. the stored bytes are uncorrupted.
        """
        abspath = self._blob_abspath(content_hash)
        if not abspath.exists():
            return False
        digest = hashlib.sha256(abspath.read_bytes()).hexdigest()
        return digest == content_hash

    def stats(self) -> Dict[str, Any]:
        """Return aggregate statistics for the store.

        Keys
        ----
        total_snapshots:
            Number of live (non-tombstoned) snapshots.
        counts_by_type:
            Mapping of artifact type tag to live snapshot count.
        total_bytes:
            Sum of blob sizes over live snapshots.
        dedupe_hits:
            Number of :meth:`put` calls whose content already existed
            (reconstructed from the index, so it survives reopening).
        """
        entries = self._read_index()
        live: Dict[str, Dict[str, Any]] = {}
        dedupe_hits = 0
        for entry in entries:
            if entry.get("action") == "delete":
                live.pop(entry.get("hash", ""), None)
                continue
            if entry.get("dedupe"):
                dedupe_hits += 1
            content_hash = entry.get("hash", "")
            if content_hash:
                live[content_hash] = entry

        counts_by_type: Dict[str, int] = {}
        total_bytes = 0
        for entry in live.values():
            atype = str(entry.get("artifact_type", ""))
            counts_by_type[atype] = counts_by_type.get(atype, 0) + 1
            total_bytes += int(entry.get("size_bytes", 0))

        return {
            "total_snapshots": len(live),
            "counts_by_type": counts_by_type,
            "total_bytes": total_bytes,
            "dedupe_hits": dedupe_hits,
        }
