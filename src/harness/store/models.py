"""SQLite schema and row conversion helpers for the Harness trace store.

Uses raw :py:mod:`sqlite3` — no ORM — so that the module has zero external
dependencies and ships with the Python standard library.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from harness.core.types import TraceRecord, FailureSignature, Verdict


# ---------------------------------------------------------------------------
# SQL DDL
# ---------------------------------------------------------------------------

_TRACES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS traces (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id      TEXT NOT NULL UNIQUE,
    scenario_id   TEXT NOT NULL,
    harness_version TEXT NOT NULL,
    timestamp     TEXT NOT NULL,
    inputs        TEXT,
    outputs       TEXT,
    verdict       TEXT,
    verifier_results TEXT,
    latency_ms    REAL,
    cost_usd      REAL,
    metadata      TEXT
);
"""

_CLUSTERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS clusters (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id    TEXT NOT NULL UNIQUE,
    pattern       TEXT NOT NULL,
    surfaces      TEXT,
    verifier_type TEXT,
    frequency     INTEGER NOT NULL DEFAULT 0,
    first_seen    TEXT,
    last_seen     TEXT
);
"""

_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_traces_scenario ON traces(scenario_id);
CREATE INDEX IF NOT EXISTS idx_traces_verdict   ON traces(verdict);
CREATE INDEX IF NOT EXISTS idx_traces_version   ON traces(harness_version);
CREATE INDEX IF NOT EXISTS idx_traces_timestamp ON traces(timestamp);
"""

_INSERT_TRACE_SQL = """
INSERT INTO traces (
    trace_id, scenario_id, harness_version, timestamp,
    inputs, outputs, verdict, verifier_results,
    latency_ms, cost_usd, metadata
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_CLUSTER_SQL = """
INSERT OR REPLACE INTO clusters (
    cluster_id, pattern, surfaces, verifier_type,
    frequency, first_seen, last_seen
) VALUES (?, ?, ?, ?, ?, ?, ?)
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_db(conn: sqlite3.Connection) -> None:
    """Create the Harness trace-store tables and indexes if they do not exist.

    Parameters
    ----------
    conn:
        An open SQLite connection.  The function does **not** commit or close
        the connection — callers remain in control of the transaction scope.
    """
    conn.execute(_TRACES_TABLE_SQL)
    conn.execute(_CLUSTERS_TABLE_SQL)
    for stmt in _INDEX_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def row_to_trace_record(row: sqlite3.Row) -> TraceRecord:
    """Convert a DB row (from ``SELECT * FROM traces``) into a
    :class:`TraceRecord` dataclass."""
    ts_raw = row["timestamp"]
    timestamp = datetime.fromisoformat(ts_raw) if isinstance(ts_raw, str) else ts_raw

    return TraceRecord(
        trace_id=row["trace_id"],
        scenario_id=row["scenario_id"],
        harness_version=row["harness_version"],
        timestamp=timestamp,
        inputs=_json_or(row["inputs"], {}),
        outputs=_json_or(row["outputs"], {}),
        verdict=_parse_verdict(row["verdict"]),
        verifier_results=_json_or(row["verifier_results"], {}),
        latency_ms=row["latency_ms"] if row["latency_ms"] is not None else 0.0,
        cost_usd=row["cost_usd"] if row["cost_usd"] is not None else 0.0,
        metadata=_json_or(row["metadata"], {}),
    )


def trace_record_to_row(trace: TraceRecord) -> Tuple:
    """Serialize a :class:`TraceRecord` into the tuple expected by the
    ``INSERT INTO traces ...`` statement."""
    return (
        trace.trace_id,
        trace.scenario_id,
        trace.harness_version,
        trace.timestamp.isoformat() if isinstance(trace.timestamp, datetime) else str(trace.timestamp),
        json.dumps(trace.inputs, default=str) if trace.inputs else None,
        json.dumps(trace.outputs, default=str) if trace.outputs else None,
        trace.verdict.name if trace.verdict else None,
        json.dumps(trace.verifier_results, default=str) if trace.verifier_results else None,
        trace.latency_ms,
        trace.cost_usd,
        json.dumps(trace.metadata, default=str) if trace.metadata else None,
    )


def row_to_failure_signature(row: sqlite3.Row) -> FailureSignature:
    """Convert a DB row from the ``clusters`` table into a
    :class:`FailureSignature` dataclass."""
    fs_raw = row["first_seen"]
    ls_raw = row["last_seen"]

    first_seen = datetime.fromisoformat(fs_raw) if isinstance(fs_raw, str) else fs_raw
    last_seen = datetime.fromisoformat(ls_raw) if isinstance(ls_raw, str) else ls_raw

    return FailureSignature(
        cluster_id=row["cluster_id"],
        pattern=row["pattern"],
        surfaces=_json_or(row["surfaces"], []),
        verifier_type=row["verifier_type"] or "",
        frequency=row["frequency"],
        first_seen=first_seen,
        last_seen=last_seen,
    )


def failure_signature_to_row(sig: FailureSignature) -> Tuple:
    """Serialize a :class:`FailureSignature` into a tuple for
    ``INSERT OR REPLACE INTO clusters ...``."""
    return (
        sig.cluster_id,
        sig.pattern,
        json.dumps(sig.surfaces) if sig.surfaces else None,
        sig.verifier_type,
        sig.frequency,
        sig.first_seen.isoformat() if isinstance(sig.first_seen, datetime) else str(sig.first_seen) if sig.first_seen else None,
        sig.last_seen.isoformat() if isinstance(sig.last_seen, datetime) else str(sig.last_seen) if sig.last_seen else None,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_or(raw: Optional[str], default: Any) -> Any:
    """Safely parse a JSON string, returning *default* on failure or when
    *raw* is ``None``."""
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _parse_verdict(raw: Optional[str]) -> Verdict:
    """Parse a verdict string back into a :class:`Verdict` enum member.

    Supports both name-based lookup (``"PASS"`` → ``Verdict.PASS``) and
    falls back to ``Verdict.SKIP`` on unknown values.
    """
    if not raw:
        return Verdict.SKIP
    try:
        return Verdict[raw]
    except KeyError:
        return Verdict.SKIP


def now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()
