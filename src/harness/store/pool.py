"""Bounded SQLite connection pool with WAL-tuned pragmas and health checks.

The reflex tier (:mod:`harness.reflex`) records every millisecond decision to
the :class:`~harness.store.trace_store.TraceStore`, which raises the write
rate by an order of magnitude.  Thread-local connections do not scale to
100+ concurrent writers (unbounded connection growth, ``database is locked``
errors).  :class:`SQLiteConnectionPool` provides a bounded, thread-safe pool
of pre-configured WAL connections that back the store in high-write
deployments.

Only the Python standard library is used (:mod:`sqlite3`, :mod:`queue`,
:mod:`threading`).

.. note::
   Pooling ``":memory:"`` databases is only meaningful with a shared-cache
   URI (e.g. ``"file:harness-mem?mode=memory&cache=shared"``); a plain
   ``":memory:"`` path gives every pooled connection its own private,
   empty database.  Prefer a real file path for pooled deployments.
"""

from __future__ import annotations

import queue
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

__all__ = ["SQLiteConnectionPool", "PoolExhaustedError"]


class PoolExhaustedError(Exception):
    """Raised when :meth:`SQLiteConnectionPool.acquire` cannot provide a
    connection within the configured timeout because all ``max_size``
    connections are checked out."""


class SQLiteConnectionPool:
    """Bounded pool of SQLite connections with health checks.

    Connections are created lazily, up to ``max_size``.  Every connection is
    configured for high-concurrency WAL workloads::

        PRAGMA journal_mode=WAL
        PRAGMA synchronous=NORMAL
        PRAGMA journal_size_limit=67108864   -- 64MB
        PRAGMA mmap_size=268435456           -- 256MB
        PRAGMA cache_size=-64000             -- 64MB
        PRAGMA busy_timeout=<timeout*1000>

    Connections use ``check_same_thread=False`` and autocommit mode
    (``isolation_level=None``), matching :class:`~harness.store.trace_store.TraceStore`
    per-statement semantics.

    Parameters
    ----------
    db_path:
        Filesystem path to the SQLite database (or a shared-cache URI for
        in-memory databases — see the module note).
    max_size:
        Maximum number of simultaneous connections.  Defaults to 8.
    timeout:
        Seconds :meth:`acquire` waits for a free connection before raising
        :class:`PoolExhaustedError`; also used for the SQLite
        ``busy_timeout`` pragma.  Defaults to 30.0.
    """

    def __init__(self, db_path: str, max_size: int = 8, timeout: float = 30.0) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        if timeout <= 0:
            raise ValueError("timeout must be > 0")
        self._db_path = db_path
        self._max_size = max_size
        self._timeout = float(timeout)
        self._available: "queue.Queue[sqlite3.Connection]" = queue.Queue()
        self._all: List[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self._created = 0
        self._closed = False

    # -- Properties ----------------------------------------------------------

    @property
    def db_path(self) -> str:
        """The database path this pool connects to."""
        return self._db_path

    @property
    def max_size(self) -> int:
        """Maximum number of pooled connections."""
        return self._max_size

    @property
    def timeout(self) -> float:
        """Acquire / busy-timeout in seconds."""
        return self._timeout

    @property
    def closed(self) -> bool:
        """Whether :meth:`close` has been called."""
        return self._closed

    @property
    def size(self) -> int:
        """Number of connections created so far (checked-out + available)."""
        with self._lock:
            return self._created

    # -- Connection lifecycle ------------------------------------------------

    def _create_connection(self) -> sqlite3.Connection:
        """Open and configure a new WAL-tuned connection."""
        conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
            isolation_level=None,  # autocommit, matching TraceStore semantics
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA journal_size_limit=67108864;")
        conn.execute("PRAGMA mmap_size=268435456;")
        conn.execute("PRAGMA cache_size=-64000;")
        conn.execute(f"PRAGMA busy_timeout={int(self._timeout * 1000)};")
        return conn

    def _borrow(self) -> sqlite3.Connection:
        """Take a connection from the pool, creating one if below max_size.

        Raises
        ------
        PoolExhaustedError
            If no connection becomes available within ``timeout`` seconds.
        RuntimeError
            If the pool has been closed.
        """
        if self._closed:
            raise RuntimeError("SQLiteConnectionPool is closed")

        # Fast path: reuse an idle connection.
        try:
            return self._available.get_nowait()
        except queue.Empty:
            pass

        # Grow the pool lazily if we are below max_size.
        with self._lock:
            if self._closed:
                raise RuntimeError("SQLiteConnectionPool is closed")
            if self._created < self._max_size:
                conn = self._create_connection()
                self._all.append(conn)
                self._created += 1
                return conn

        # Pool is at capacity: block until a connection is released.
        try:
            return self._available.get(timeout=self._timeout)
        except queue.Empty:
            raise PoolExhaustedError(
                f"No SQLite connection available within {self._timeout:.3f}s "
                f"(max_size={self._max_size} all checked out)"
            ) from None

    @contextmanager
    def acquire(self) -> Iterator[sqlite3.Connection]:
        """Context manager yielding a pooled connection.

        The connection is automatically returned to the pool when the block
        exits — even on exception, in which case any open transaction is
        rolled back first.

        Example
        -------
        >>> with pool.acquire() as conn:            # doctest: +SKIP
        ...     conn.execute("INSERT INTO traces ...")
        """
        conn = self._borrow()
        try:
            yield conn
        except BaseException:
            self._rollback_if_dirty(conn)
            raise
        finally:
            self.release(conn)

    @staticmethod
    def _rollback_if_dirty(conn: sqlite3.Connection) -> None:
        """Roll back *conn* if it has an open transaction; never raises."""
        try:
            if conn.in_transaction:
                conn.rollback()
        except sqlite3.Error:
            pass

    def release(self, conn: sqlite3.Connection) -> None:
        """Return *conn* to the pool.

        A dirty connection (open transaction) is rolled back before being
        requeued.  If the pool has been closed the connection is closed
        instead of being requeued.
        """
        self._rollback_if_dirty(conn)
        if self._closed:
            try:
                conn.close()
            except sqlite3.Error:
                pass
            return
        self._available.put(conn)

    # -- Health & maintenance ------------------------------------------------

    def health_check(self) -> Dict[str, Any]:
        """Inspect every pooled connection and the WAL state.

        Runs ``PRAGMA quick_check`` on each *available* connection (checked
        out connections are reported as ``"checked_out"`` and not touched, to
        avoid racing with their owner thread), plus a passive WAL checkpoint
        status probe.

        Returns
        -------
        dict
            ``db_path``, ``max_size``, ``size``, ``in_use``, ``available``,
            ``closed``, ``healthy`` (bool), ``connections`` (list of
            per-connection dicts with ``status``/``quick_check``), and
            ``wal_checkpoint`` (``busy``/``log_frames``/``checkpointed_frames``).
        """
        with self._lock:
            all_conns = list(self._all)
            created = self._created
            closed = self._closed

        # Drain the available queue so health checks never race with a
        # thread actively using a checked-out connection.
        idle: List[sqlite3.Connection] = []
        while True:
            try:
                idle.append(self._available.get_nowait())
            except queue.Empty:
                break

        idle_ids = {id(c) for c in idle}
        connections: List[Dict[str, Any]] = []
        healthy = not closed
        wal_status: Dict[str, Any] = {
            "busy": None, "log_frames": None, "checkpointed_frames": None,
        }

        try:
            for idx, conn in enumerate(all_conns):
                if id(conn) not in idle_ids:
                    connections.append({"index": idx, "status": "checked_out"})
                    continue
                entry: Dict[str, Any] = {"index": idx}
                try:
                    row = conn.execute("PRAGMA quick_check").fetchone()
                    quick = row[0] if row else "unknown"
                    entry["quick_check"] = quick
                    entry["status"] = "ok" if quick == "ok" else "error"
                    if quick != "ok":
                        healthy = False
                except sqlite3.Error as exc:
                    entry["quick_check"] = f"error: {exc}"
                    entry["status"] = "error"
                    healthy = False
                connections.append(entry)

            # WAL checkpoint status from any idle connection.
            for conn in idle:
                try:
                    row = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
                    if row is not None:
                        wal_status = {
                            "busy": int(row[0]),
                            "log_frames": int(row[1]),
                            "checkpointed_frames": int(row[2]),
                        }
                    break
                except sqlite3.Error:
                    continue
        finally:
            for conn in idle:
                self._available.put(conn)

        in_use = created - len(idle)
        return {
            "db_path": self._db_path,
            "max_size": self._max_size,
            "size": created,
            "in_use": in_use,
            "available": len(idle),
            "closed": closed,
            "healthy": healthy,
            "connections": connections,
            "wal_checkpoint": wal_status,
        }

    def close(self) -> None:
        """Close every pooled connection.  Idempotent.

        Connections currently checked out are closed when they are released
        back to the pool instead of being requeued.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True

        while True:
            try:
                conn = self._available.get_nowait()
            except queue.Empty:
                break
            try:
                conn.close()
            except sqlite3.Error:
                pass

    # -- Context manager -----------------------------------------------------

    def __enter__(self) -> "SQLiteConnectionPool":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
