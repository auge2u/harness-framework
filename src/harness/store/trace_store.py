"""TraceStore — persistent storage and querying for Harness trace records.

The store uses raw :py:mod:`sqlite3` and the schema defined in
:mod:`harness.store.models`.  It is designed to be:

* **Thread-safe for reads** — WAL mode allows concurrent readers.
* **Thread-safe for writes** — each thread gets its own connection, or a
  bounded pooled connection when a :class:`SQLiteConnectionPool` is supplied.
* **Zero-dependency** — only the Python standard library.

.. note::
   **Recommended construction for high-write deployments** (e.g. the reflex
   tier recording every millisecond decision): pass a shared bounded pool so
   connection growth stays bounded under 100+ concurrent writers::

       pool = SQLiteConnectionPool(path, max_size=8)
       store = TraceStore(path, pool=pool)
"""

from __future__ import annotations

import csv
import json
import math
import sqlite3
import threading
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from harness.core.types import TraceRecord, FailureSignature, Verdict

from harness.store.models import (
    init_db,
    row_to_trace_record,
    trace_record_to_row,
    row_to_failure_signature,
    failure_signature_to_row,
    _INSERT_TRACE_SQL,
    _INSERT_CLUSTER_SQL,
)
from harness.store.pool import SQLiteConnectionPool


# ---------------------------------------------------------------------------
# TraceStore
# ---------------------------------------------------------------------------

class TraceStore:
    """Persistent SQLite-backed store for Harness trace records.

    Parameters
    ----------
    db_path:
        Filesystem path to the SQLite database.  Defaults to ``":memory:"``
        which is useful for unit tests and ephemeral sessions.
    pool:
        Optional :class:`~harness.store.pool.SQLiteConnectionPool`.  When
        provided, every store operation borrows a connection from the pool
        instead of using thread-local connections — the recommended setup
        for high-write deployments (bounded connections, WAL-tuned pragmas).
        When ``None`` (the default), behavior is exactly the historical
        thread-local implementation.  The store does **not** take ownership
        of the pool: callers remain responsible for ``pool.close()``.
    """

    def __init__(
        self,
        db_path: str = ":memory:",
        pool: Optional[SQLiteConnectionPool] = None,
    ) -> None:
        self._pool = pool
        if pool is not None:
            # The pool is the source of truth for the database location.
            self._db_path = pool.db_path if db_path == ":memory:" else db_path
        else:
            self._db_path = db_path
        self._local = threading.local()
        # For :memory: databases we need a shared connection because each
        # sqlite3.connect(":memory:") call creates a *separate* in-memory
        # database.  We keep a master connection and share it across threads.
        self._memory_conn: Optional[sqlite3.Connection] = None
        if pool is not None:
            with pool.acquire() as conn:
                init_db(conn)
                self._create_indices(conn)
                conn.commit()
        elif self._db_path == ":memory:":
            self._memory_conn = sqlite3.connect(":memory:")
            self._memory_conn.row_factory = sqlite3.Row
            init_db(self._memory_conn)
            self._create_indices(self._memory_conn)
            self._memory_conn.commit()
        else:
            self._init_schema()

    def _create_indices(self, conn: sqlite3.Connection) -> None:
        """Create indices for common query patterns."""
        conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_scenario ON traces(scenario_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_verdict ON traces(verdict)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_version ON traces(harness_version)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_timestamp ON traces(timestamp)")

    # -- Connection management ------------------------------------------------

    def _connection(self) -> sqlite3.Connection:
        """Return a connection bound to the current thread."""
        # For :memory:, return the shared master connection
        if self._memory_conn is not None:
            return self._memory_conn

        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            self._local.conn = conn
        return conn

    @contextmanager
    def _op_conn(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection for a single store operation.

        With a pool, a connection is borrowed from the pool and returned
        automatically; without a pool this yields the historical thread-local
        (or shared in-memory) connection unchanged.
        """
        if self._pool is not None:
            with self._pool.acquire() as conn:
                yield conn
        else:
            yield self._connection()

    def _init_schema(self) -> None:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        init_db(conn)
        self._create_indices(conn)
        conn.commit()
        conn.close()

    # -- Public API -----------------------------------------------------------

    def record(self, trace: TraceRecord) -> None:
        """Persist a single :class:`TraceRecord` into the store."""
        with self._op_conn() as conn:
            row = trace_record_to_row(trace)
            conn.execute(_INSERT_TRACE_SQL, row)

    def query(
        self,
        scenario_id: Optional[str] = None,
        verdict: Optional[Verdict] = None,
        surface: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        harness_version: Optional[str] = None,
    ) -> List[TraceRecord]:
        """Query traces with optional filters.

        All parameters are AND-combined.  The *surface* filter inspects the
        ``metadata`` JSON column for the key ``surface`` or a key named
        ``surfaces`` that contains a list.

        Returns
        -------
        list[TraceRecord]
            Ordered by ``timestamp`` descending (newest first).
        """
        with self._op_conn() as conn:
            conditions: List[str] = ["1=1"]
            params: List[Any] = []

            if scenario_id is not None:
                conditions.append("scenario_id = ?")
                params.append(scenario_id)
            if verdict is not None:
                conditions.append("verdict = ?")
                params.append(verdict.name if isinstance(verdict, Verdict) else verdict)
            if since is not None:
                conditions.append("timestamp >= ?")
                params.append(since)
            if until is not None:
                conditions.append("timestamp <= ?")
                params.append(until)
            if harness_version is not None:
                conditions.append("harness_version = ?")
                params.append(harness_version)

            sql = "SELECT * FROM traces WHERE " + " AND ".join(conditions)

            if surface is not None:
                sql += (
                    " AND ("
                    "   json_extract(metadata, '$.surface') = ?"
                    "   OR json_extract(metadata, '$.surfaces') LIKE ?"
                    "   OR json_extract(metadata, '$.surfaces') LIKE ?"
                    "   OR json_extract(metadata, '$.surfaces') LIKE ?"
                    ")"
                )
                params.append(surface)
                params.append(f'%"{surface}"%')
                params.append(f'["{surface}"]%')
                params.append(f'%,"{surface}"]%')

            sql += " ORDER BY timestamp DESC"

            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()
            return [row_to_trace_record(r) for r in rows]

    def get_surface_failure_rate(
        self, surface: str, version: Optional[str] = None
    ) -> float:
        """Return the failure rate for a given surface.

        The rate is ``failures / total`` for traces whose metadata
        contains *surface*.  Returns ``0.0`` when no matching traces exist.
        """
        with self._op_conn() as conn:
            base = """
                SELECT verdict, COUNT(*) AS cnt
                FROM traces
                WHERE (
                    json_extract(metadata, '$.surface') = ?
                    OR json_extract(metadata, '$.surfaces') LIKE ?
                    OR json_extract(metadata, '$.surfaces') LIKE ?
                    OR json_extract(metadata, '$.surfaces') LIKE ?
                )
            """
            params: List[Any] = [
                surface, f'%"{surface}"%', f'["{surface}"]%', f'%,"{surface}"]%',
            ]
            if version is not None:
                base += " AND harness_version = ?"
                params.append(version)
            base += " GROUP BY verdict"

            rows = conn.execute(base, params).fetchall()

        total = 0
        failures = 0
        for r in rows:
            count = r["cnt"]
            total += count
            v = r["verdict"]
            if v in (Verdict.FAIL.name, Verdict.PARTIAL.name, Verdict.ERROR.name if hasattr(Verdict, 'ERROR') else Verdict.FAIL.name):
                failures += count

        return failures / total if total > 0 else 0.0

    def get_cost_by_surface(
        self, surface: str, since: Optional[str] = None
    ) -> float:
        """Sum the ``cost_usd`` column for traces whose metadata contains
        *surface*."""
        with self._op_conn() as conn:
            sql = """
                SELECT COALESCE(SUM(cost_usd), 0.0) AS total
                FROM traces
                WHERE (
                    json_extract(metadata, '$.surface') = ?
                    OR json_extract(metadata, '$.surfaces') LIKE ?
                    OR json_extract(metadata, '$.surfaces') LIKE ?
                    OR json_extract(metadata, '$.surfaces') LIKE ?
                )
            """
            params: List[Any] = [
                surface, f'%"{surface}"%', f'["{surface}"]%', f'%,"{surface}"]%',
            ]
            if since is not None:
                sql += " AND timestamp >= ?"
                params.append(since)

            row = conn.execute(sql, params).fetchone()
            return float(row["total"]) if row else 0.0

    def get_performance_trend(
        self, scenario_id: str, window: int = 10
    ) -> List[float]:
        """Return the last *window* aggregate scores for *scenario_id*.

        Scores are derived from ``verifier_results`` JSON (key
        ``aggregate_score``) or mapped from the verdict (PASS=1.0,
        PARTIAL=0.5, FAIL=0.0).
        """
        with self._op_conn() as conn:
            sql = """
                SELECT verdict, verifier_results
                FROM traces
                WHERE scenario_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """
            rows = conn.execute(sql, (scenario_id, window)).fetchall()

        scores: List[float] = []
        for r in rows:
            vr = _json_or(r["verifier_results"], {})
            if isinstance(vr, dict) and "aggregate_score" in vr:
                scores.append(float(vr["aggregate_score"]))
            else:
                v = r["verdict"]
                scores.append(_verdict_to_score(v))
        return scores

    def export(self, path: str, format: str = "json") -> None:
        """Export all traces to *path* as JSON array or CSV.

        Parameters
        ----------
        path:
            Destination file path.
        fmt:
            Either ``"json"`` or ``"csv"``.

        Raises
        ------
        ValueError
            If *fmt* is unsupported.
        """
        if format not in ("json", "csv"):
            raise ValueError(f"Unsupported format: {format!r}. Use 'json' or 'csv'.")

        records = self.query()
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        dicts = [_trace_to_dict(t) for t in records]

        if format == "json":
            with dest.open("w", encoding="utf-8") as fh:
                json.dump(dicts, fh, indent=2, default=str)
        else:
            headers = [
                "trace_id", "scenario_id", "harness_version", "timestamp",
                "inputs", "outputs", "verdict", "verifier_results",
                "latency_ms", "cost_usd", "metadata",
            ]
            with dest.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=headers)
                writer.writeheader()
                if dicts:
                    writer.writerows(dicts)

    def get_failure_signatures(self, limit: int = 100) -> List[FailureSignature]:
        """Return up to *limit* failure signatures from the ``clusters`` table,
        ordered by ``frequency`` descending."""
        with self._op_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM clusters ORDER BY frequency DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [row_to_failure_signature(r) for r in rows]

    def record_cluster(self, sig: FailureSignature) -> None:
        """Persist a :class:`FailureSignature` to the ``clusters`` table."""
        with self._op_conn() as conn:
            conn.execute(_INSERT_CLUSTER_SQL, failure_signature_to_row(sig))

    # -- Analytics -----------------------------------------------------------

    def get_time_series(
        self,
        metric: str = "score",
        interval: str = "day",
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return time-bucketed metrics.

        Parameters
        ----------
        metric:
            One of ``"score"`` | ``"cost"`` | ``"latency"`` | ``"failure_rate"``.
        interval:
            One of ``"hour"`` | ``"day"`` | ``"week"``.
        since, until:
            Optional ISO timestamp bounds.

        Returns
        -------
        list[dict]
            Each dict has ``bucket`` (str), ``value`` (float), and ``count`` (int).
        """
        fmt_map = {
            "hour": "%Y-%m-%d %H:00",
            "day": "%Y-%m-%d",
            "week": "%Y-%W",
        }
        fmt = fmt_map.get(interval, "%Y-%m-%d")

        # Build the aggregate expression based on metric
        if metric == "score":
            agg_expr = """
                COALESCE(
                    AVG(
                        CASE
                            WHEN json_extract(verifier_results, '$.aggregate_score') IS NOT NULL
                            THEN json_extract(verifier_results, '$.aggregate_score')
                            WHEN verdict = 'PASS' THEN 1.0
                            WHEN verdict = 'PARTIAL' THEN 0.5
                            ELSE 0.0
                        END
                    ), 0.0
                )
            """
        elif metric == "cost":
            agg_expr = "COALESCE(SUM(cost_usd), 0.0)"
        elif metric == "latency":
            agg_expr = "COALESCE(AVG(latency_ms), 0.0)"
        elif metric == "failure_rate":
            agg_expr = """
                COALESCE(
                    CAST(SUM(CASE WHEN verdict IN ('FAIL', 'PARTIAL', 'ERROR') THEN 1 ELSE 0 END) AS REAL)
                    / NULLIF(COUNT(*), 0),
                    0.0
                )
            """
        else:
            raise ValueError(f"Unknown metric: {metric!r}")

        sql = f"""
            SELECT
                strftime('{fmt}', timestamp) AS bucket,
                {agg_expr} AS value,
                COUNT(*) AS count
            FROM traces
            WHERE 1=1
        """
        params: List[Any] = []
        if since is not None:
            sql += " AND timestamp >= ?"
            params.append(since)
        if until is not None:
            sql += " AND timestamp <= ?"
            params.append(until)
        sql += " GROUP BY bucket ORDER BY bucket"

        with self._op_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [
                {"bucket": r["bucket"], "value": float(r["value"]), "count": int(r["count"])}
                for r in rows
            ]

    def get_trend(
        self, scenario_id: str, metric: str = "score", window: int = 10
    ) -> Dict[str, Any]:
        """Compute trend statistics for a scenario.

        Returns
        -------
        dict
            ``values`` (list[float]), ``mean`` (float), ``std`` (float),
            ``slope`` (float), ``direction`` ("improving" | "stable" | "degrading").
        """
        if metric == "score":
            sql = """
                SELECT
                    CASE
                        WHEN json_extract(verifier_results, '$.aggregate_score') IS NOT NULL
                        THEN json_extract(verifier_results, '$.aggregate_score')
                        WHEN verdict = 'PASS' THEN 1.0
                        WHEN verdict = 'PARTIAL' THEN 0.5
                        ELSE 0.0
                    END AS val
                FROM traces
                WHERE scenario_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """
        elif metric == "cost":
            sql = """
                SELECT cost_usd AS val
                FROM traces
                WHERE scenario_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """
        elif metric == "latency":
            sql = """
                SELECT latency_ms AS val
                FROM traces
                WHERE scenario_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """
        else:
            raise ValueError(f"Unknown metric: {metric!r}")

        with self._op_conn() as conn:
            rows = conn.execute(sql, (scenario_id, window)).fetchall()
        values = [float(r["val"]) if r["val"] is not None else 0.0 for r in rows]
        values.reverse()  # chronological order for slope calculation

        n = len(values)
        mean = sum(values) / n if n > 0 else 0.0

        # Standard deviation
        if n > 1:
            variance = sum((v - mean) ** 2 for v in values) / (n - 1)
            std = math.sqrt(variance)
        else:
            std = 0.0

        # Simple linear regression (least squares) slope
        if n >= 2:
            x_mean = (n - 1) / 2.0
            y_mean = mean
            numerator = sum((i - x_mean) * (values[i] - y_mean) for i in range(n))
            denominator = sum((i - x_mean) ** 2 for i in range(n))
            slope = numerator / denominator if denominator != 0 else 0.0
        else:
            slope = 0.0

        # Direction based on slope and std
        if n == 0:
            direction = "stable"
        elif std == 0:
            direction = "stable" if abs(slope) < 0.001 else ("improving" if slope > 0 else "degrading")
        else:
            relative_slope = slope / std if std > 0 else 0.0
            if relative_slope > 0.1:
                direction = "improving"
            elif relative_slope < -0.1:
                direction = "degrading"
            else:
                direction = "stable"

        return {
            "values": values,
            "mean": mean,
            "std": std,
            "slope": slope,
            "direction": direction,
        }

    def get_surface_analytics(self) -> Dict[str, Dict[str, Any]]:
        """Aggregate analytics per surface.

        Returns
        -------
        dict
            ``{"surface_name": {"total_runs": int, "failure_rate": float,
            "avg_cost": float, "avg_latency": float}}``.
        """
        with self._op_conn() as conn:
            sql = """
                SELECT
                    COALESCE(json_extract(metadata, '$.surface'), 'unknown') AS surface_name,
                    COUNT(*) AS total_runs,
                    CAST(SUM(CASE WHEN verdict IN ('FAIL', 'PARTIAL', 'ERROR') THEN 1 ELSE 0 END) AS REAL)
                        / NULLIF(COUNT(*), 0) AS failure_rate,
                    COALESCE(AVG(cost_usd), 0.0) AS avg_cost,
                    COALESCE(AVG(latency_ms), 0.0) AS avg_latency
                FROM traces
                WHERE json_extract(metadata, '$.surface') IS NOT NULL
                GROUP BY surface_name
            """
            rows = conn.execute(sql).fetchall()
            result: Dict[str, Dict[str, Any]] = {}
            for r in rows:
                surface = r["surface_name"]
                if surface:
                    result[surface] = {
                        "total_runs": int(r["total_runs"]),
                        "failure_rate": float(r["failure_rate"]) if r["failure_rate"] is not None else 0.0,
                        "avg_cost": float(r["avg_cost"]) if r["avg_cost"] is not None else 0.0,
                        "avg_latency": float(r["avg_latency"]) if r["avg_latency"] is not None else 0.0,
                    }

            # Also handle surfaces stored as lists in $.surfaces
            sql_list = """
                SELECT metadata
                FROM traces
                WHERE json_extract(metadata, '$.surfaces') IS NOT NULL
            """
            list_rows = conn.execute(sql_list).fetchall()
        for r in list_rows:
            meta = _json_or(r["metadata"], {})
            surfaces = meta.get("surfaces", [])
            if not isinstance(surfaces, list):
                continue
            for surface in surfaces:
                if surface not in result:
                    result[surface] = {
                        "total_runs": 0,
                        "failure_rate": 0.0,
                        "avg_cost": 0.0,
                        "avg_latency": 0.0,
                    }
                result[surface]["total_runs"] += 1

        return result

    def get_anomaly_traces(self, threshold_sigma: float = 2.0) -> List[TraceRecord]:
        """Find traces with anomalous scores (outside threshold_sigma std devs from mean).

        Returns
        -------
        list[TraceRecord]
            Statistical outlier traces.
        """
        with self._op_conn() as conn:
            # First get all scores
            sql = """
                SELECT
                    CASE
                        WHEN json_extract(verifier_results, '$.aggregate_score') IS NOT NULL
                        THEN json_extract(verifier_results, '$.aggregate_score')
                        WHEN verdict = 'PASS' THEN 1.0
                        WHEN verdict = 'PARTIAL' THEN 0.5
                        ELSE 0.0
                    END AS score
                FROM traces
            """
            rows = conn.execute(sql).fetchall()
        scores = [float(r["score"]) for r in rows]

        if len(scores) < 2:
            return []

        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / (len(scores) - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0

        if std == 0:
            return []

        lower_bound = mean - threshold_sigma * std
        upper_bound = mean + threshold_sigma * std

        # Now fetch traces that are outliers
        sql = """
            SELECT *
            FROM traces
            WHERE (
                CASE
                    WHEN json_extract(verifier_results, '$.aggregate_score') IS NOT NULL
                    THEN json_extract(verifier_results, '$.aggregate_score')
                    WHEN verdict = 'PASS' THEN 1.0
                    WHEN verdict = 'PARTIAL' THEN 0.5
                    ELSE 0.0
                END
            ) < ? OR (
                CASE
                    WHEN json_extract(verifier_results, '$.aggregate_score') IS NOT NULL
                    THEN json_extract(verifier_results, '$.aggregate_score')
                    WHEN verdict = 'PASS' THEN 1.0
                    WHEN verdict = 'PARTIAL' THEN 0.5
                    ELSE 0.0
                END
            ) > ?
            ORDER BY timestamp DESC
        """
        with self._op_conn() as conn:
            cursor = conn.execute(sql, (lower_bound, upper_bound))
            rows = cursor.fetchall()
            return [row_to_trace_record(r) for r in rows]

    # -- Maintenance ---------------------------------------------------------

    def optimize_storage(self) -> Dict[str, Any]:
        """Compact and optimize the underlying database.

        Runs ``PRAGMA wal_checkpoint(TRUNCATE)``, ``PRAGMA optimize``, and
        ``VACUUM``.  The VACUUM is guarded: if it conflicts with concurrent
        WAL activity (``database is locked``) it is skipped and reported via
        ``vacuum_ok``/``vacuum_error`` instead of raising.

        Returns
        -------
        dict
            ``page_size``, ``pages_before``, ``pages_after``,
            ``bytes_before``, ``bytes_after``, ``wal_checkpoint`` (dict with
            ``busy``/``log_frames``/``checkpointed_frames`` or ``None``),
            ``vacuum_ok`` (bool), ``vacuum_error`` (str or None).
        """
        own_conn = False
        if self._memory_conn is not None:
            conn = self._memory_conn
        else:
            # Use a dedicated connection so maintenance never fights pooled
            # or thread-local connections for a transaction slot.
            conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                isolation_level=None,
            )
            own_conn = True

        try:
            page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
            pages_before = int(conn.execute("PRAGMA page_count").fetchone()[0])

            wal_checkpoint: Optional[Dict[str, Any]] = None
            try:
                row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if row is not None:
                    wal_checkpoint = {
                        "busy": int(row[0]),
                        "log_frames": int(row[1]),
                        "checkpointed_frames": int(row[2]),
                    }
            except sqlite3.OperationalError:
                wal_checkpoint = None

            try:
                conn.execute("PRAGMA optimize")
            except sqlite3.OperationalError:
                pass

            vacuum_ok = True
            vacuum_error: Optional[str] = None
            try:
                conn.execute("VACUUM")
            except sqlite3.OperationalError as exc:
                # WAL-autocheckpoint or concurrent-writer conflict — skip.
                vacuum_ok = False
                vacuum_error = str(exc)

            pages_after = int(conn.execute("PRAGMA page_count").fetchone()[0])

            return {
                "page_size": page_size,
                "pages_before": pages_before,
                "pages_after": pages_after,
                "bytes_before": pages_before * page_size,
                "bytes_after": pages_after * page_size,
                "wal_checkpoint": wal_checkpoint,
                "vacuum_ok": vacuum_ok,
                "vacuum_error": vacuum_error,
            }
        finally:
            if own_conn:
                conn.close()

    def close(self) -> None:
        """Close the connection(s).

        Does **not** close an externally supplied
        :class:`~harness.store.pool.SQLiteConnectionPool` — the pool's owner
        remains responsible for its lifecycle.
        """
        if self._memory_conn is not None:
            try:
                self._memory_conn.close()
            except Exception:
                pass
            self._memory_conn = None
        else:
            conn = getattr(self._local, "conn", None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
                self._local.conn = None

    def __enter__(self) -> "TraceStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_or(raw: Optional[str], default: Any) -> Any:
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _verdict_to_score(v: Optional[str]) -> float:
    from harness.core.types import Verdict
    mapping = {
        Verdict.PASS.name: 1.0,
        Verdict.PARTIAL.name: 0.5,
        Verdict.FAIL.name: 0.0,
        Verdict.SKIP.name: 0.0,
    }
    return mapping.get(v or "", 0.0)


def _trace_to_dict(t: TraceRecord) -> Dict[str, Any]:
    """Convert a :class:`TraceRecord` to a plain dict for serialisation."""
    return {
        "trace_id": t.trace_id,
        "scenario_id": t.scenario_id,
        "harness_version": t.harness_version,
        "timestamp": t.timestamp.isoformat() if isinstance(t.timestamp, datetime) else str(t.timestamp),
        "inputs": t.inputs,
        "outputs": t.outputs,
        "verdict": t.verdict.name if t.verdict else None,
        "verifier_results": t.verifier_results,
        "latency_ms": t.latency_ms,
        "cost_usd": t.cost_usd,
        "metadata": t.metadata,
    }
