"""Tests for harness.store.pool (SQLiteConnectionPool) and pooled TraceStore.

Covers pool lifecycle, WAL pragmas, exhaustion/back-pressure, TraceStore
integration, storage optimization, backward compatibility, and concurrent
stress scenarios (100 writers / mixed readers+writers).

All tests use ``tmp_path`` database files.  ``:memory:`` is intentionally
avoided for pooling semantics: each ``sqlite3.connect(":memory:")`` call
creates a *separate* database unless a shared-cache URI is used, which would
break pool invariants.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from harness.core.types import TraceRecord, Verdict
from harness.store.pool import PoolExhaustedError, SQLiteConnectionPool
from harness.store.trace_store import TraceStore


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def make_trace(trace_id: str, scenario_id: str = "s1", verdict: Verdict = Verdict.PASS,
               surface: str = "api", cost: float = 0.01, latency: float = 5.0) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        scenario_id=scenario_id,
        harness_version="0.4.0",
        timestamp=datetime.now(timezone.utc),
        inputs={"q": trace_id},
        outputs={"r": "ok"},
        verdict=verdict,
        verifier_results={"aggregate_score": 1.0 if verdict == Verdict.PASS else 0.0},
        latency_ms=latency,
        cost_usd=cost,
        metadata={"surface": surface},
    )


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "pooled_traces.db")


@pytest.fixture()
def pool(db_path: str) -> SQLiteConnectionPool:
    p = SQLiteConnectionPool(db_path, max_size=4, timeout=10.0)
    yield p
    p.close()


# ---------------------------------------------------------------------------
# 1. Pool lifecycle: lazy creation, max_size, reuse, exhaustion
# ---------------------------------------------------------------------------

class TestPoolLifecycle:
    def test_lazy_creation(self, pool: SQLiteConnectionPool) -> None:
        assert pool.size == 0  # nothing created until first acquire

    def test_first_acquire_creates_connection(self, pool: SQLiteConnectionPool) -> None:
        with pool.acquire():
            pass
        assert pool.size == 1

    def test_max_size_enforcement(self, db_path: str) -> None:
        pool = SQLiteConnectionPool(db_path, max_size=2, timeout=5.0)
        with pool.acquire():
            with pool.acquire():
                assert pool.size == 2
        assert pool.size == 2
        pool.close()

    def test_acquire_release_reuses_connection(self, pool: SQLiteConnectionPool) -> None:
        with pool.acquire() as conn1:
            pass
        with pool.acquire() as conn2:
            pass
        assert conn1 is conn2
        assert pool.size == 1

    def test_exhaustion_raises_on_timeout(self, db_path: str) -> None:
        pool = SQLiteConnectionPool(db_path, max_size=2, timeout=0.2)
        with pool.acquire():
            with pool.acquire():
                start = time.monotonic()
                with pytest.raises(PoolExhaustedError):
                    with pool.acquire():
                        pass
                elapsed = time.monotonic() - start
        assert elapsed < 5.0  # raised via timeout, not a hang
        pool.close()

    def test_release_after_exhaustion_unblocks(self, db_path: str) -> None:
        pool = SQLiteConnectionPool(db_path, max_size=1, timeout=5.0)
        with pool.acquire() as conn:
            pass
        with pool.acquire() as conn2:  # would raise if not released
            assert conn2 is conn
        pool.close()

    def test_release_rolls_back_dirty_connection(self, db_path: str) -> None:
        pool = SQLiteConnectionPool(db_path, max_size=1, timeout=5.0)
        with pool.acquire() as conn:
            conn.execute("CREATE TABLE t (x INTEGER)")
            conn.execute("BEGIN")
            conn.execute("INSERT INTO t VALUES (1)")
            assert conn.in_transaction
            pool.release(conn)
        with pool.acquire() as conn:
            assert not conn.in_transaction
            # rolled back: the insert is gone
            assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0
        pool.close()

    def test_invalid_max_size_rejected(self, db_path: str) -> None:
        with pytest.raises(ValueError):
            SQLiteConnectionPool(db_path, max_size=0)

    def test_invalid_timeout_rejected(self, db_path: str) -> None:
        with pytest.raises(ValueError):
            SQLiteConnectionPool(db_path, timeout=0)

    def test_acquire_after_close_raises(self, db_path: str) -> None:
        pool = SQLiteConnectionPool(db_path, max_size=2)
        pool.close()
        with pytest.raises(RuntimeError):
            with pool.acquire():
                pass


# ---------------------------------------------------------------------------
# 2. Health check + close semantics
# ---------------------------------------------------------------------------

class TestPoolHealthAndClose:
    def test_health_check_keys(self, pool: SQLiteConnectionPool) -> None:
        with pool.acquire():
            pass
        health = pool.health_check()
        for key in ("db_path", "max_size", "size", "in_use", "available",
                    "closed", "healthy", "connections", "wal_checkpoint"):
            assert key in health, f"missing health key: {key}"

    def test_health_check_quick_check_ok(self, pool: SQLiteConnectionPool) -> None:
        with pool.acquire() as conn:
            conn.execute("CREATE TABLE hc (x INTEGER)")
        health = pool.health_check()
        assert health["healthy"] is True
        assert health["size"] == 1
        assert health["available"] == 1
        assert health["in_use"] == 0
        statuses = [c.get("quick_check") for c in health["connections"]]
        assert statuses == ["ok"]

    def test_health_check_wal_checkpoint_status(self, pool: SQLiteConnectionPool) -> None:
        with pool.acquire():
            pass
        wal = pool.health_check()["wal_checkpoint"]
        assert "busy" in wal and "log_frames" in wal and "checkpointed_frames" in wal

    def test_health_check_reports_checked_out(self, pool: SQLiteConnectionPool) -> None:
        with pool.acquire():
            health = pool.health_check()
            assert health["in_use"] == 1
            assert health["available"] == 0
            assert health["connections"][0]["status"] == "checked_out"

    def test_close_is_idempotent(self, pool: SQLiteConnectionPool) -> None:
        with pool.acquire():
            pass
        pool.close()
        pool.close()  # double-close must be safe
        assert pool.closed is True

    def test_close_actually_closes_connections(self, db_path: str) -> None:
        pool = SQLiteConnectionPool(db_path, max_size=2)
        with pool.acquire() as conn:
            pass
        pool.close()
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_context_manager_closes(self, db_path: str) -> None:
        with SQLiteConnectionPool(db_path, max_size=2) as pool:
            with pool.acquire():
                pass
        assert pool.closed is True


# ---------------------------------------------------------------------------
# 3. WAL pragmas
# ---------------------------------------------------------------------------

class TestPoolPragmas:
    def test_journal_mode_is_wal(self, pool: SQLiteConnectionPool) -> None:
        with pool.acquire() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    def test_synchronous_normal(self, pool: SQLiteConnectionPool) -> None:
        with pool.acquire() as conn:
            sync = conn.execute("PRAGMA synchronous").fetchone()[0]
        assert sync == 1  # NORMAL

    def test_busy_timeout_applied(self, db_path: str) -> None:
        pool = SQLiteConnectionPool(db_path, max_size=1, timeout=3.0)
        with pool.acquire() as conn:
            busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert busy == 3000
        pool.close()

    def test_cache_size_applied(self, pool: SQLiteConnectionPool) -> None:
        with pool.acquire() as conn:
            cache = conn.execute("PRAGMA cache_size").fetchone()[0]
        assert cache == -64000

    def test_mmap_size_applied(self, pool: SQLiteConnectionPool) -> None:
        with pool.acquire() as conn:
            mmap = conn.execute("PRAGMA mmap_size").fetchone()[0]
        assert mmap == 268435456

    def test_journal_size_limit_applied(self, pool: SQLiteConnectionPool) -> None:
        with pool.acquire() as conn:
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
        assert limit == 67108864

    def test_row_factory_is_row(self, pool: SQLiteConnectionPool) -> None:
        with pool.acquire() as conn:
            assert conn.row_factory is sqlite3.Row


# ---------------------------------------------------------------------------
# 4. TraceStore with pool: roundtrip + analytics parity
# ---------------------------------------------------------------------------

def _seed(store: TraceStore, n: int = 10) -> None:
    for i in range(n):
        verdict = Verdict.PASS if i % 3 else Verdict.FAIL
        store.record(make_trace(f"seed-{i}", scenario_id=f"s{i % 2}", verdict=verdict))


class TestPooledTraceStore:
    def test_record_query_roundtrip(self, db_path: str) -> None:
        with SQLiteConnectionPool(db_path, max_size=4) as pool:
            store = TraceStore(pool=pool)
            store.record(make_trace("rt-1"))
            results = store.query()
            assert len(results) == 1
            assert results[0].trace_id == "rt-1"
            assert results[0].metadata["surface"] == "api"
        store.close()

    def test_query_filters_work(self, db_path: str) -> None:
        with SQLiteConnectionPool(db_path, max_size=4) as pool:
            store = TraceStore(pool=pool)
            _seed(store)
            assert len(store.query(scenario_id="s1")) == 5
            assert len(store.query(verdict=Verdict.FAIL)) == 4
            assert len(store.query(surface="api")) == 10
        store.close()

    def test_analytics_identical_to_non_pool(self, db_path: str, tmp_path: Path) -> None:
        plain_path = str(tmp_path / "plain.db")
        with SQLiteConnectionPool(db_path, max_size=4) as pool:
            pooled = TraceStore(pool=pool)
            plain = TraceStore(plain_path)
            _seed(pooled)
            _seed(plain)

            assert pooled.get_surface_failure_rate("api") == pytest.approx(
                plain.get_surface_failure_rate("api"))
            assert pooled.get_cost_by_surface("api") == pytest.approx(
                plain.get_cost_by_surface("api"))
            assert pooled.get_performance_trend("s0") == plain.get_performance_trend("s0")
            ts_pooled = pooled.get_time_series(metric="score", interval="day")
            ts_plain = plain.get_time_series(metric="score", interval="day")
            assert ts_pooled == ts_plain
            trend_pooled = pooled.get_trend("s0")
            trend_plain = plain.get_trend("s0")
            assert trend_pooled == trend_plain
            sa_pooled = pooled.get_surface_analytics()
            sa_plain = plain.get_surface_analytics()
            assert sa_pooled == sa_plain
            pooled.close()
            plain.close()

    def test_schema_initialized_via_pool(self, db_path: str) -> None:
        with SQLiteConnectionPool(db_path, max_size=2) as pool:
            TraceStore(pool=pool)
            with pool.acquire() as conn:
                tables = {
                    r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
            assert {"traces", "clusters"} <= tables

    def test_pool_connections_bounded_under_store_use(self, db_path: str) -> None:
        with SQLiteConnectionPool(db_path, max_size=3) as pool:
            store = TraceStore(pool=pool)
            _seed(store, n=50)
            store.query()
            store.get_surface_analytics()
            assert pool.size <= 3
        store.close()


# ---------------------------------------------------------------------------
# 5-6. Stress tests
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestPooledStress:
    def test_100_writers_2000_records(self, db_path: str) -> None:
        """100 threads x 20 records into one pooled store (max_size=8)."""
        pool = SQLiteConnectionPool(db_path, max_size=8, timeout=30.0)
        store = TraceStore(pool=pool)
        errors: List[BaseException] = []
        n_threads, per_thread = 100, 20

        def worker(tid: int) -> None:
            try:
                for i in range(per_thread):
                    store.record(make_trace(f"stress-{tid}-{i}", scenario_id=f"s{tid % 5}"))
            except BaseException as exc:  # noqa: BLE001 - collected for assertion
                errors.append(exc)

        start = time.monotonic()
        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.monotonic() - start

        assert errors == [], f"errors during stress: {errors[:3]}"
        assert len(store.query()) == n_threads * per_thread
        assert pool.size <= 8
        rec_per_sec = (n_threads * per_thread) / elapsed if elapsed > 0 else float("inf")
        print(f"\n[stress] {n_threads * per_thread} records in {elapsed:.2f}s "
              f"({rec_per_sec:.0f} rec/s)")
        store.close()
        pool.close()

    def test_concurrent_readers_and_writers(self, db_path: str) -> None:
        """50 reader threads querying while 20 writer threads write."""
        pool = SQLiteConnectionPool(db_path, max_size=8, timeout=30.0)
        store = TraceStore(pool=pool)
        _seed(store, n=20)
        errors: List[BaseException] = []

        def reader(rid: int) -> None:
            try:
                for _ in range(30):
                    store.query(scenario_id=f"s{rid % 2}")
                    store.get_surface_failure_rate("api")
                    # Yield briefly so hot readers cannot starve writers
                    # (queue.put can be re-stolen by a looping thread before
                    # a blocked waiter is scheduled).
                    time.sleep(0.001)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def writer(wid: int) -> None:
            try:
                for i in range(25):
                    store.record(make_trace(f"rw-{wid}-{i}"))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        readers = [threading.Thread(target=reader, args=(r,)) for r in range(50)]
        writers = [threading.Thread(target=writer, args=(w,)) for w in range(20)]
        for t in readers + writers:
            t.start()
        for t in readers + writers:
            t.join()

        assert errors == [], f"errors during mixed stress: {errors[:3]}"
        assert len(store.query()) == 20 + 20 * 25
        store.close()
        pool.close()


# ---------------------------------------------------------------------------
# 7. optimize_storage
# ---------------------------------------------------------------------------

class TestOptimizeStorage:
    def test_optimize_returns_page_stats(self, db_path: str) -> None:
        with SQLiteConnectionPool(db_path, max_size=4) as pool:
            store = TraceStore(pool=pool)
            _seed(store, n=30)
            stats = store.optimize_storage()
            for key in ("page_size", "pages_before", "pages_after",
                        "bytes_before", "bytes_after", "wal_checkpoint",
                        "vacuum_ok", "vacuum_error"):
                assert key in stats, f"missing key: {key}"
            assert stats["pages_before"] > 0
            assert stats["pages_after"] > 0
            assert stats["page_size"] > 0
            assert stats["vacuum_ok"] is True
            store.close()

    def test_optimize_does_not_corrupt_subsequent_writes(self, db_path: str) -> None:
        with SQLiteConnectionPool(db_path, max_size=4) as pool:
            store = TraceStore(pool=pool)
            _seed(store, n=10)
            store.optimize_storage()
            store.record(make_trace("post-opt"))
            results = store.query()
            assert len(results) == 11
            assert results[0].trace_id == "post-opt"
            # analytics still consistent
            assert store.get_surface_failure_rate("api") >= 0.0
            store.close()

    def test_optimize_wal_checkpoint_reported(self, db_path: str) -> None:
        with SQLiteConnectionPool(db_path, max_size=4) as pool:
            store = TraceStore(pool=pool)
            _seed(store, n=5)
            stats = store.optimize_storage()
            wal = stats["wal_checkpoint"]
            assert wal is not None
            assert wal["busy"] == 0
            store.close()

    def test_optimize_non_pool_store(self, db_path: str) -> None:
        store = TraceStore(db_path)
        _seed(store, n=10)
        stats = store.optimize_storage()
        assert stats["pages_after"] > 0
        store.record(make_trace("np-post"))
        assert len(store.query()) == 11
        store.close()

    def test_optimize_memory_store(self) -> None:
        store = TraceStore(":memory:")
        _seed(store, n=5)
        stats = store.optimize_storage()
        assert stats["pages_before"] > 0
        assert len(store.query()) == 5
        store.close()


# ---------------------------------------------------------------------------
# 8. Backward compatibility (no pool)
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    def test_file_store_without_pool(self, db_path: str) -> None:
        store = TraceStore(db_path)
        store.record(make_trace("bc-1"))
        results = store.query()
        assert len(results) == 1
        assert results[0].trace_id == "bc-1"
        store.close()

    def test_memory_store_without_pool(self) -> None:
        store = TraceStore(":memory:")
        _seed(store, n=6)
        assert len(store.query()) == 6
        assert len(store.query(scenario_id="s0")) == 3
        store.close()

    def test_non_pool_analytics_unchanged(self, db_path: str) -> None:
        store = TraceStore(db_path)
        _seed(store, n=9)
        # 3 FAIL of 9 -> failure rate 1/3
        assert store.get_surface_failure_rate("api") == pytest.approx(1 / 3)
        ts = store.get_time_series(metric="failure_rate")
        assert ts and ts[0]["count"] == 9
        store.close()

    def test_pool_is_optional_keyword(self, db_path: str) -> None:
        # signature: TraceStore(db_path=":memory:", pool=None)
        store = TraceStore(db_path, pool=None)
        store.record(make_trace("kw-1"))
        assert len(store.query()) == 1
        store.close()

    def test_export_still_works(self, db_path: str, tmp_path: Path) -> None:
        store = TraceStore(db_path)
        _seed(store, n=3)
        out = str(tmp_path / "export.json")
        store.export(out, format="json")
        assert Path(out).exists()
        store.close()
