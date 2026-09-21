# Harness Framework — Architectural & Engineering Review

**Reviewer**: Senior Staff Engineer (Codex Review Style)  
**Scope**: 10 core files across the Harness Framework  
**Date**: 2025-01-15  
**Verdict**: **request_changes** — multiple critical bugs, thread-safety violations, and runtime errors must be fixed before merge.

---

## File-by-File Review

### 1. `src/harness/runners/circuit_runner.py` — Parallel Execution

#### CRITICAL-1: Missing `import copy` causes runtime `NameError`
- **Severity**: critical
- **Issue**: `_apply_proposal()` calls `copy.deepcopy(base)` at line 470, but `copy` is not imported.
- **Location**: Lines 470, and static method at lines 403-470
- **Recommendation**: Add `import copy` to the module imports.

#### MAJOR-1: Silent data loss on budget exceeded
- **Severity**: major
- **Issue**: `run_variants()` raises `BudgetExceededError` after sorting partial results, but the caller receives no results — all completed variant work is lost.
- **Location**: Lines 255-260
- **Recommendation**: Return completed results alongside the exception, or define a `CircuitBatchResult` dataclass that carries both `results` and `budget_exceeded: bool`.

#### MAJOR-2: Held-out phase bypasses failure clustering and trace persistence
- **Severity**: major
- **Issue**: The optional held-out run at lines 267-293 calls `runner.run()` directly without updating `failure_clusters` or ensuring `CircuitResult` fields are fully populated. The `best_result.cost_usd += ho_cost` mutation is also not thread-safe if results are shared.
- **Location**: Lines 267-293
- **Recommendation**: Extract a `_finalize_result()` helper that consistently populates all `CircuitResult` fields, clusters failures, and persists traces.

#### MINOR-1: `_apply_proposal` mutates input `base` config in-place for scope changes
- **Severity**: minor
- **Issue**: For `tune_threshold`, `add_verifier`, etc., the base config is mutated directly before `apply_changes()` is called. This violates the method's docstring claim of being "non-mutating".
- **Location**: Lines 440-461
- **Recommendation**: Deep-copy `base` once at the start of the static method.

#### MINOR-2: `_approximate_p_value` uses unpaired test on potentially paired data
- **Severity**: minor
- **Issue**: The normal difference-of-means test assumes independent samples, but per-scenario scores across variants are paired (same scenarios). A paired t-test or Wilcoxon signed-rank would be more appropriate.
- **Location**: Lines 488-509
- **Recommendation**: Document the assumption or switch to a paired test.

---

### 2. `src/harness/store/trace_store.py` — SQLite Trace Storage

#### CRITICAL-2: `:memory:` connection shared across threads without locking
- **Severity**: critical
- **Issue**: `self._memory_conn` is a single `sqlite3.Connection` returned to all threads. SQLite connections are **not** thread-safe; concurrent use from multiple threads will corrupt memory or raise `sqlite3.ProgrammingError`.
- **Location**: Lines 56-62, 75-79
- **Recommendation**: Serialize all `:memory:` access with a `threading.Lock()`, or use `sqlite3.connect("file::memory:?cache=shared", uri=True)` with explicit locking.

#### CRITICAL-3: `check_same_thread=False` with per-thread connections is misleading
- **Severity**: critical
- **Issue**: `_connection()` sets `check_same_thread=False` for file DBs but stores connections in `threading.local()`. If a connection object leaks across threads (e.g., via closure capture), SQLite will not detect misuse, leading to undefined behavior.
- **Location**: Lines 83-86
- **Recommendation**: Remove `check_same_thread=False` since you already use `threading.local()`.

#### MAJOR-3: `record()` never commits for in-memory DBs
- **Severity**: major
- **Issue**: `record()` executes `INSERT` but never calls `conn.commit()`. For in-memory DBs (default), the shared connection uses default transaction mode, so inserts are invisible to other connections and may be lost on process exit.
- **Location**: Lines 105-109
- **Recommendation**: Add `conn.commit()` after executing the insert, or set `isolation_level=None` on `_memory_conn`.

#### MAJOR-4: Unbounded `history` list growth in `_usage`
- **Severity**: major  
- **Issue**: `data_gateway.py` (related) appends every call to `history` without trimming. Over long runs this is an unbounded memory leak. However, `trace_store.py` also has no LIMIT on `query()` when no filters are applied — a full table scan could OOM.
- **Location**: `data_gateway.py` lines 128-132; `trace_store.py` lines 151-171
- **Recommendation**: Add a `max_history` cap in the gateway; add a default `LIMIT` to `query()` or use pagination.

#### MINOR-3: JSON surface matching is fragile
- **Severity**: minor
- **Issue**: The `LIKE` patterns for `$.surfaces` JSON array matching (`%"surface"%`, `["surface"]%`, etc.) will false-positive on substrings and can be evaded by whitespace variations in JSON serialization.
- **Location**: Lines 153-165, 192-194, 219-230
- **Recommendation**: Use SQLite's `json_each()` table-valued function for robust JSON array membership tests.

#### MINOR-4: `get_anomaly_traces()` computes stats in Python, not SQL
- **Severity**: minor
- **Issue**: The method fetches all scores into Python to compute mean/std, then runs a second query. This is O(n) memory and two round-trips.
- **Location**: Lines 559-622
- **Recommendation**: Use a window function or CTE to compute mean/std in SQL, then filter in a single query.

---

### 3. `src/harness/loops/self_harness.py` — Self-Improvement Loop

#### CRITICAL-4: `_apply_proposal` violates non-mutation contract
- **Severity**: critical
- **Issue**: The method docstring says "return a new config (non-mutating)" but scope-based changes like `tune_threshold` mutate `base` directly. Worse, the deep-copy is done *inside* the loop on a per-change basis, so earlier mutations may leak into later iterations.
- **Location**: Lines 566-599
- **Recommendation**: Deep-copy `base` exactly once before the loop, then apply all changes to the copy.

#### MAJOR-5: `step()` evaluates only the first proposal, wasting the other `n-1`
- **Severity**: major
- **Issue**: `propose(n=3)` generates up to 3 proposals, but `step()` unconditionally evaluates only `proposals[0]`. The remaining proposals are discarded, making `max_proposals_per_cycle` misleading.
- **Location**: Lines 387-393
- **Recommendation**: Evaluate all proposals (or top-k by estimated impact) and pick the best, or rename the parameter to `max_proposals_generated`.

#### MAJOR-6: Acceptance suite called with `context=None` breaks gates that need it
- **Severity**: major
- **Issue**: `step()` passes `None` as the `context` argument to `evaluate_all()`. Gates like `TraceabilityGate` or future gates may rely on context attributes.
- **Location**: Line 438
- **Recommendation**: Pass a real `RunContext` object, or at minimum document that `context` may be `None` in the `AcceptanceGate` contract.

#### MINOR-5: Risk assessment logic is mathematically incorrect
- **Severity**: minor
- **Issue**: `failure_rate = 1.0 - held_in_score if held_in_score <= 1.0 else 0.0` — if `held_in_score` is negative, `failure_rate` becomes > 1.0, which then falls into the "high" risk bucket. The guard should be `0.0 <= held_in_score <= 1.0`.
- **Location**: Line 322
- **Recommendation**: Clamp `held_in_score` to `[0, 1]` before computing failure rate.

#### MINOR-6: `run()` return type is lossy
- **Severity**: minor
- **Issue**: `run()` strips gate reports and promotion records, returning only 3-tuples. Callers lose critical governance data.
- **Location**: Lines 528-529
- **Recommendation**: Return full 5-tuples, or add a `return_full: bool = False` parameter.

#### INFO-1: No early termination on repeated identical proposals
- **Severity**: info
- **Issue**: The loop could generate the same proposal (e.g., "lower threshold for X") repeatedly if the failure rate stays > 30%.
- **Location**: `propose()` lines 163-232
- **Recommendation**: Deduplicate proposals by `(surface, action)` before returning.

---

### 4. `src/harness/analysis/clusterer.py` — Failure Clustering

#### MAJOR-7: `get_relationship_impact()` is O(s³ × c) — cubic in surfaces
- **Severity**: major
- **Issue**: For every pair of surfaces (s²/2), it iterates all clusters (c). With 100 surfaces and 10k clusters, this is ~50M operations.
- **Location**: Lines 257-297
- **Recommendation**: Pre-compute an inverted index `surface -> set(cluster_ids)`, then use set intersections for O(s² × avg_clusters_per_surface).

#### MINOR-7: `cluster()` builds `trace_map` but does not use it for all traces
- **Severity**: minor
- **Issue**: The `trace_map` only contains traces from the current batch, but `_clusters` may reference trace IDs from previous batches. The result dict at line 233 silently drops missing traces.
- **Location**: Lines 225-234
- **Recommendation**: Document the behavior, or fetch missing traces from the store.

#### MINOR-8: `_extract_signature()` duplicates surface extraction logic
- **Severity**: minor
- **Issue**: The same surface/metadata extraction pattern appears in `add_trace()` (lines 192-199) and `_extract_signature()` (lines 98-106). This is a maintenance risk.
- **Location**: Lines 86-125, 192-199
- **Recommendation**: Extract a `_extract_surfaces_from_trace()` helper.

---

### 5. `src/harness/policy.py` — Policy Engine

#### MAJOR-8: `_shannon_entropy` uses `chr(x)` on full byte range — incorrect for Unicode
- **Severity**: major
- **Issue**: The entropy calculation loops `for x in range(256)` and counts `chr(x)` in the string. For Unicode strings, `chr(128)` through `chr(255)` are valid but this assumes a byte model. More importantly, `chr(x)` for x>127 will count multi-byte UTF-8 sequences incorrectly. The entropy result is biased and unreliable for non-ASCII secrets.
- **Location**: Lines 626-636
- **Recommendation**: Encode the string to bytes first (`data.encode('utf-8', errors='ignore')`), then compute entropy over byte values.

#### MINOR-9: Secret regexes use `re.IGNORECASE` but patterns are already case-insensitive
- **Severity**: minor
- **Issue**: Patterns like `[aA][pP][iI]` are manually case-insensitive; compiling with `re.IGNORECASE` is redundant and slower.
- **Location**: Lines 221-248, 273-275
- **Recommendation**: Simplify patterns or remove `re.IGNORECASE`.

#### MINOR-10: `_is_false_positive` is overly aggressive
- **Severity**: minor
- **Issue**: Any line containing "example" or "test" (even in unrelated words like "testament") skips all secret matches on that line.
- **Location**: Lines 638-677
- **Recommendation**: Use word-boundary matching for false-positive indicators.

#### INFO-2: `check_edit_permission()` stops at first matching rule
- **Severity**: info
- **Issue**: The loop `break`s after the first match, so rule ordering matters but is not validated. A later, more specific rule will be silently ignored.
- **Location**: Lines 362-368
- **Recommendation**: Implement rule specificity scoring (e.g., longest prefix match) or document that rules are evaluated in registration order.

---

### 6. `src/harness/acceptance.py` — Acceptance Gates

#### MINOR-11: `RegressionGate` requires both deltas to be positive for PASS
- **Severity**: minor
- **Issue**: The condition `delta_in >= 0 and delta_ho >= 0 and max(delta_in, delta_ho) > 0` requires improvement on both splits. A large held-out improvement with tiny held-in regression (within tolerance) gets WARNING instead of PASS.
- **Location**: Lines 160-166
- **Recommendation**: Consider whether the held-out split is optional; if so, adjust the logic.

#### MINOR-12: `TraceabilityGate` uses `truthiness` check that may reject valid `0` values
- **Severity**: minor
- **Issue**: `not evidence[field_name] and evidence[field_name] != 0` correctly handles `0`, but empty strings and empty lists also pass (are considered "present"). An empty `scenarios_run` list would pass when it shouldn't.
- **Location**: Lines 364-368
- **Recommendation**: Explicitly check `evidence[field_name] is not None` or validate types per field.

#### INFO-3: `AcceptanceSuite` catches all exceptions silently in `evaluate_all()`
- **Severity**: info
- **Issue**: Gate exceptions are caught and converted to `FAIL` reports, but the traceback is lost, making debugging difficult.
- **Location**: Lines 596-604
- **Recommendation**: Log the full exception with `logging.exception()` before converting to a report.

---

### 7. `src/harness/patch.py` — Patch Primitive

#### MINOR-13: `from_dict()` recursive deserialization can cause infinite recursion on circular inverse
- **Severity**: minor
- **Issue**: If a serialized patch has a circular reference in `inverse` (which `to_dict()` does not prevent), `from_dict()` will recurse until stack overflow.
- **Location**: Lines 122-166
- **Recommendation**: Add a recursion limit or track visited IDs during deserialization.

#### MINOR-14: `__post_init__` auto-generates inverse without deep-copying `before`/`after`
- **Severity**: minor
- **Issue**: The inverse patch shares references to `self.after` and `self.before`. Mutating the inverse's `before` (which is the original's `after`) mutates the original's data.
- **Location**: Lines 66-92
- **Recommendation**: Use `copy.deepcopy()` for `before` and `after` when creating the inverse.

#### INFO-4: `diff_surfaces()` silently drops surfaces without a `name` key
- **Severity**: info
- **Issue**: Surfaces with empty or missing names are skipped without warning.
- **Location**: Lines 318-330
- **Recommendation**: Add a warning log or include unnamed surfaces with a generated ID.

---

### 8. `src/harness/core/config.py` — Configuration

#### MINOR-15: `apply_changes()` does not validate threshold ranges
- **Severity**: minor
- **Issue**: `set_thresholds` directly assigns floats without checking `[0, 1]` bounds or the `auto_accept > auto_reject` invariant.
- **Location**: Lines 439-444
- **Recommendation**: Re-use `validate()` after applying changes.

#### MINOR-16: `from_yaml()` silently requires PyYAML but declares no dependency
- **Severity**: minor
- **Issue**: The optional import is handled gracefully, but downstream packaging may not include `pyyaml`.
- **Location**: Lines 88-115
- **Recommendation**: Add `pyyaml` as an `extras_require` in `setup.py`/`pyproject.toml`.

#### INFO-5: `__post_init__` normalizes mutable defaults but not nested ones
- **Severity**: info
- **Issue**: `self.surfaces = []` if `None`, but elements within `surfaces` are not validated.
- **Location**: Lines 32-47
- **Recommendation**: Surface validation could be deeper (e.g., check `Surface` instances).

---

### 9. `src/harness/core/registry.py` — Plugin Registry

#### MINOR-17: `emit()` catches and swallows all callback exceptions
- **Severity**: minor
- **Issue**: One failing hook callback silently prevents all subsequent callbacks from running.
- **Location**: Lines 159-169
- **Recommendation**: Continue iterating after catching, but log the exception. The current code already continues, but silently — add logging.

#### MINOR-18: `discover()` suppresses all import errors silently
- **Severity**: minor
- **Issue**: A broken plugin module is skipped without any indication, making debugging difficult.
- **Location**: Lines 175-210
- **Recommendation**: Log import failures at `warning` or `debug` level.

#### INFO-6: Registry stores classes, not instances — no lifecycle management
- **Severity**: info
- **Issue**: Each call site must instantiate. There's no caching, dependency injection, or teardown.
- **Location**: Whole module
- **Recommendation**: Consider a factory registry or DI container for complex plugins.

---

### 10. `src/harness/gateway/data_gateway.py` — Rate-Limited Gateway

#### MAJOR-9: `_TokenBucket` is not thread-safe
- **Severity**: major
- **Issue**: `consume()` and `refill()` read/write shared state (`self.tokens`, `self.last_update`) without any synchronization. Concurrent calls will race and corrupt token counts.
- **Location**: Lines 17-43
- **Recommendation**: Add a `threading.Lock()` per bucket, or use `asyncio.Lock()` if the gateway is strictly async.

#### MAJOR-10: `request()` is async but performs zero I/O
- **Severity**: major
- **Issue**: The method is declared `async` but contains no `await`. This misleads callers into thinking it's non-blocking, while it actually runs synchronously on the event loop. Worse, it returns a mock response — this is a stub pretending to be an implementation.
- **Location**: Lines 104-141
- **Recommendation**: Either implement real async I/O with `aiohttp`/`httpx`, or rename to `_mock_request()` and document it as a placeholder.

#### MINOR-19: No budget reset mechanism for daily rollover
- **Severity**: minor
- **Issue**: `_daily_spent` increments forever. There's no timestamp tracking to reset at midnight.
- **Location**: Lines 59, 113-115, 122-123
- **Recommendation**: Track `last_reset_date` and auto-reset when the date changes.

#### MINOR-20: `request()` records successful call before making the actual request
- **Severity**: minor
- **Issue**: The usage is recorded and budget deducted before any I/O happens. If the request later fails, the budget is still consumed.
- **Location**: Lines 122-132
- **Recommendation**: Record usage only after a successful response (or split into "attempted" vs "succeeded" counters).

---

## Top 5 Critical Issues (Codebase-Wide)

| Rank | Issue | File | Why It Matters |
|------|-------|------|----------------|
| 1 | **Missing `import copy`** | `circuit_runner.py:470` | **Runtime crash** — every call to `_apply_proposal` raises `NameError`. |
| 2 | **Thread-unsafe shared SQLite connection** | `trace_store.py:56-79` | **Data corruption / crashes** — `:memory:` DB shared across threads without locking violates SQLite's thread-safety model. |
| 3 | **No `commit()` on `record()`** | `trace_store.py:105-109` | **Silent data loss** — in-memory traces are never committed and vanish on connection close. |
| 4 | **`_apply_proposal` mutates input config** | `self_harness.py:566-599` | **State corruption** — the "non-mutating" method corrupts the live config, causing unpredictable proposal side-effects. |
| 5 | **Race condition in `_TokenBucket`** | `data_gateway.py:26-35` | **Rate-limit bypass** — concurrent requests can over-consume tokens, defeating the rate limiter. |

---

## Top 5 Architectural Strengths

| Rank | Strength | Evidence |
|------|----------|----------|
| 1 | **Strong separation of concerns** | Policy engine, acceptance gates, patch primitives, and trace storage are cleanly decoupled with well-defined interfaces. |
| 2 | **Comprehensive policy framework** | `policy.py` covers privilege escalation, secret scanning, prompt safety, and held-out protection — defense-in-depth is explicit. |
| 3 | **Invertible patch design** | `HarnessPatch` auto-generates inverse patches in `__post_init__`, enabling first-class rollback — a rare and valuable design choice. |
| 4 | **Pluggable registry with discovery** | `PluginRegistry` supports auto-discovery, lifecycle hooks, and namespace separation (verifiers / proposers / runners). |
| 5 | **Rich acceptance gate taxonomy** | Seven distinct gates (regression, scope, security, traceability, rollback, cost, determinism) provide fine-grained promotion control. |

---

## Cross-Cutting Concerns

### Type Safety
- **Good**: Most core types use dataclasses with `__post_init__` normalization.
- **Bad**: Heavy use of `Any` in public APIs (`patch: Any`, `context: Any`, `evidence: Dict[str, Any]`). Consider `Protocol` classes for `PatchLike` and `ContextLike`.
- **Inconsistent**: `data_gateway.py` uses `list[str]` (Python 3.9+) while the rest of the codebase uses `typing.List[str]`.

### Testability
- **Good**: Most classes accept dependencies via constructor injection (`registry`, `trace_store`, etc.).
- **Bad**: `SingleRunner` hardcodes `registry.get_verifier("exact")` (`runner.py:84`), ignoring config. `SelfHarnessLoop` instantiates `SingleRunner` internally, making it hard to mock.
- **Bad**: `DataGateway.request()` is a mock — there is no real implementation to test against.

### Concurrency
- **Critical**: `TraceStore` `:memory:` mode and `DataGateway._TokenBucket` both have thread-safety violations.
- **Moderate**: `CircuitRunner` uses `ThreadPoolExecutor` correctly but `_cumulative_cost` is mutated from multiple threads without synchronization (lines 253, 289, 293).

### Error Handling
- **Good**: Custom exception hierarchy (`HarnessError` subclasses) is well-structured.
- **Bad**: `registry.emit()` and `discover()` swallow exceptions silently.
- **Bad**: `runner.py` catches broad `Exception` in verifier lookup and silently degrades to `FAIL`.

---

## Overall Verdict

**`request_changes`**

This codebase demonstrates solid architectural thinking and good separation of concerns, but it has **multiple critical runtime bugs** (missing import, thread-safety violations, silent data loss) that must be fixed before it can be considered production-ready. Additionally, the `DataGateway` is a stub implementation masquerading as real async I/O, and the self-improvement loop discards most of its generated proposals. Fix the critical issues, add tests for concurrency and error paths, and this becomes a strong framework.

---

## Recommended Priority Order for Fixes

1. **P0**: Add `import copy` to `circuit_runner.py`
2. **P0**: Fix `TraceStore` thread safety (serialize `:memory:` access, remove `check_same_thread=False`)
3. **P0**: Add `conn.commit()` to `TraceStore.record()`
4. **P0**: Fix `_TokenBucket` thread safety with locks
5. **P1**: Fix `SelfHarnessLoop._apply_proposal()` to deep-copy once and never mutate input
6. **P1**: Implement or clearly document `DataGateway.request()` as a mock
7. **P1**: Make `SelfHarnessLoop.step()` evaluate all proposals or rename the parameter
8. **P2**: Add pagination / limits to `trace_store.query()` and `get_anomaly_traces()`
9. **P2**: Optimize `clusterer.get_relationship_impact()` to O(s²)
10. **P2**: Fix `_shannon_entropy()` to operate on bytes, not Unicode characters
