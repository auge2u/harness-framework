# Harness Framework — Enhancement Roadmap & Next Steps

## Current State (Post-Enhancement)

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Source files | 43 | 48 | +5 |
| Test files | 25 | 31 | +6 |
| Total tests | 415 | **569** | +154 |
| Test runtime | 2.1s | **1.48s** | **-30%** |
| Source lines | ~9,300 | ~10,800 | +1,500 |
| Test lines | ~4,600 | ~6,200 | +1,600 |

## Completed Improvements (9 of 20)

### Bundle 1: Governance Integration ✓
- **A2** — SelfHarnessLoop now uses AcceptanceSuite + PromotionPipeline for full governance
- **A3** — PolicyEngine validates every patch before evaluation (scope, privilege, secrets, prompts)
- **A1** — HarnessPatch ↔ HarnessProposal bidirectional conversion with auto-inverse

### Bundle 2: UX & Config ✓
- **B1** — Rich CLI output: progress bars, ASCII tables, ANSI colors, `--json` mode, `--no-color` flag
- **B2** — Config YAML export + detailed validation with warnings (held_out_ratio, cost_budget, missing surfaces/verifiers)

### Bundle 3: Data + Security + Ops ✓
- **C1** — TraceStore analytics: time-series bucketing, trend detection (slope + direction), surface analytics, anomaly detection (sigma threshold), SQLite indices
- **E1** — Hardened secret scanning: 10+ regex patterns, entropy checks, false-positive filtering (test/example/placeholder exclusion)
- **E2** — Sandbox path traversal hardening: null-byte detection, normalization, symlink checking, Unicode normalization
- **F1** — Signal handlers + graceful shutdown: SIGINT/SIGTERM capture, handler registry, singleton pattern

## P0 Bug Fixes (Post-Review)
- Fixed missing `import copy` in circuit_runner.py
- Fixed mutable class-level defaults in Scenario ABC (shared-state footgun)
- Fixed CLI to return exit code 1 when scenarios fail

---

## ⚡ RE-TIERED ROADMAP (v0.4.0) — Two-Tier System 1 / System 2 Tracks

> **This roadmap was refactored on 2026-09-21 around the Reflex Layer.**
> See **REFLEX_PLAN.md** for the full strategic plan: the uploads (Jev Primitives
> Handbook, Reflexive AI Specification Paper, Strategic Implementation Roadmap,
> qualitative_linter v1–v3, ci_runner v1–v2, skill_routing_proxy) define a
> System 1 tier (millisecond `bool`/`score`/`choice` reflexes) that the
> framework previously lacked. Rubrics become governed artifacts on the new
> 19th surface (`reflex`); meta-refinement runs through the existing
> Self-Harness patch pipeline. The 11 prompts below are preserved verbatim
> but are superseded in ordering by the T1/T2 tracks here.

### Tier 1 Track — Reflex Rollout (NEW)

| # | Item | Effort | Depends on |
|---|---|---|---|
| T1.1 | `reflex/` foundation: ReflexBackend ABC, MockReflexBackend, JevBackend, primitives, rubrics, escalation | 4h | — |
| T1.2 | QualitativeLinter (6 rules) + ReflexiveVerifier + ReflexGate | 4h | T1.1 |
| T1.3 | ReflexRouter (≤255 skills) + token-savings telemetry | 3h | T1.1 |
| T1.4 | ReflexCIRunner + `.github/workflows/reflex_ci.yml` (dogfood, advisory mode) | 3h | T1.2 |
| T1.5 | MetaRefinementAnalyzer: decision-vs-outcome audit → rubric HarnessPatch proposals | 4h | T1.2 + T2.4 |
| T1.6 | Lifecycle hooks upgrade: regex gates → `bool` semantic gates (opt-in) | 2h | T1.2 |
| T1.7 | Swarm task assignment via `choice` (task → worker role) | 3h | T1.3 |

### Tier 2 Track — Existing Prompts, Re-Sequenced

| # | Original Prompt | New ID | Note under two-tier plan |
|---|---|---|---|
| T2.1 | Prompt 1: ToolRegistry ↔ PluginRegistry unification | T2.1 | Tools become `choice`-routable by T1.3 |
| T2.2 | Prompt 2: AgentBackend integration into Runner | T2.2 | Unchanged |
| T2.3 | Prompt 3: CLI `validate` command | T2.3 | Unchanged |
| T2.4 | Prompt 4: Config snapshot versioning | T2.4 | Also versions rubric artifacts — feeds T1.5 |
| T2.5 | Prompt 6: Connection pooling + WAL | T2.5 | Priority UP: reflex tier 10×'s TraceStore writes |
| T2.6 | Prompt 7: Async DataGateway | T2.6 | Unchanged |
| T2.7 | Prompt 5: Export pipeline (Parquet + OTel) | T2.7 | Reflex decision logs = first-class export corpus |
| T2.8 | Prompt 8: Immutable audit logging | T2.8 | Must include reflex decisions + rubric lineage |
| T2.9 | Prompt 9: Health check endpoint | T2.9 | Adds reflex-backend liveness + escalation-rate probe |
| T2.10 | Prompt 10: Web dashboard | T2.10 | First-class views: escalation queue, rubric drift, autonomy phase |
| T2.11 | Prompt 11: MCP adapter | T2.11 | Router surface exposed as MCP tool |

### Execution Waves (dependency-respecting)

```
Wave A (foundation):   T1.1 → T1.2 → T1.3                      ✅ COMPLETE (860 tests)
Wave B (deployment):   T1.4 · T2.5 · T2.3        (parallel)    ✅ COMPLETE (963 tests, 2026-09-21)
Wave C (governance):   T1.5 → T1.6 · T2.4 · T2.8 (parallel)    ✅ COMPLETE (1120 tests, 2026-09-21)
Wave D (scale):        T1.7 · T2.1 → T2.2 · T2.6 · T2.7        ⬅ NEXT
Wave E (experience):   T2.9 → T2.10 · T2.11
```

### Wave C Completion Record (2026-09-21)

| Track | Delivered | Tests | Evidence |
|---|---|---|---|
| T1.6 | `lifecycle/semantic_hooks.py`: 3 reflex-backed hooks (command/secret/patch) + `register_semantic_gates` opt-in; regex hooks untouched | +32 | chain coexistence verified; fail-closed on backend errors |
| T2.4 | `store/snapshots.py`: ContentAddressedStore (sha256, sharded, dedupe, tombstones, verify) + lineage `snapshot_hash`/`checkout_by_hash`/`snapshot_rubric` | +43 | 20-thread dedupe test; reopen persistence |
| T2.8 | `audit.py`: SHA-256-chained JSONL AuditLog + `verify_chain()` (detects modify/delete/reorder/truncate) + AuditRecorder facade (policy/promotion/reflex/rubric actions) | +43 | tamper scenarios all detected; 30-thread × 10 append chain valid |
| T1.5 | `reflex/meta.py`: MetaRefinementAnalyzer (train/held-out split, per-rule precision/recall, drift windows, HarnessPatch proposals with auto-inverse, held-out patch evaluation, promote guard) + rubric v2 (`required_signals`) + R0 refinements applied | +39 | full-loop test: precision 0.2→1.0 promote=True; **dogfood 32→8 findings (−75%), secret FPs −100%, blocking −100%** |

**R1 baseline**: `docs/reflex_baseline_r1.md` — honest audit: remaining 8 N+1
flags are prose-token FPs (`objects` in docstrings); v3 backlog recorded
(pattern signals, prose suppression, corpus fixtures). Meta-refinement loop
proven end-to-end with measurable, reproducible evidence.

### Wave B Completion Record (2026-09-21)

| Track | Delivered | Tests | Evidence |
|---|---|---|---|
| T1.4 | `python3 -m harness.reflex` CLI: `--diff/--files/--phase/--summary/--json/--remediate`; git-diff parsing; dogfood default scan; `reflex_ci.yml` activated (advisory) | +42 | 963 green; R0 baseline in `docs/reflex_baseline_r0.md` |
| T2.5 | `store/pool.py` (SQLiteConnectionPool, 6 WAL pragmas, health checks) + pooled TraceStore + `optimize_storage()` | +41 | 100 threads × 20 rec → 2000/2000, 0 lock errors, ~5,377 rec/s |
| T2.3 | `harness validate <cfg> [--strict] [--json]` + legacy `--harness-validate` | +20 | exit 0/1/2 verified; JSON shape asserted |

**R0 dogfood baseline**: 40 files → 32 findings, est. precision ~28% under
MockReflexBackend (expected; see `docs/reflex_baseline_r0.md`). Rubric
refinement backlog captured there feeds T1.5. R3 gating remains correctly
blocked until a production backend + FP < 2% over 2 advisory weeks.

### Phased Autonomy (KPI-gated, see REFLEX_PLAN.md §6)

| Phase | Autonomy | Promotion criteria |
|---|---|---|
| R0 Shadow | Log only | ≥1,000 logged decisions/rule |
| R1 Advisory | Annotate | Precision ≥ 90% (n≥100/rule human audit) |
| R2 Routing | Route for real | Routing error ≤ 10% (baseline 17%) |
| R3 Gating | Block on `bool` invariants | FP block rate < 2% over 2 advisory weeks |
| R4 Remediation | Closed-loop auto-fix | Re-verify pass ≥ 95%, zero broken builds |
| R5 Meta-refinement | Rubric patches auto-promoted | Held-out log evaluation, drift alarms armed |

Demotion rule: breaching criteria in 2 consecutive windows drops one autonomy
level and files an incident trace. Autonomy is a promoted state in lineage.

---

## Remaining Improvements (11 of 20) — ORIGINAL PROMPTS (preserved)

### Prompt 1: ToolRegistry ↔ PluginRegistry Unification (A4)
**Trigger**: When you need a single registry for all plugins, tools, verifiers, and proposers.
**Action**: Create a unified registry that supports registering any plugin-like object with a typed dispatch mechanism. Refactor existing PluginRegistry and ToolRegistry to delegate to the unified registry.
**Success metric**: One registry.register() call for all types; backward compatibility preserved.
**Estimated effort**: 3 hours
**Dependencies**: None

### Prompt 2: AgentBackend Integration into Runner (A5)
**Trigger**: When you need the runner to invoke actual LLM backends instead of mock execution.
**Action**: Wire AgentBackend.complete() into SingleRunner.run() for scenarios that specify an agent. Add backend selection via config.model_defaults. Implement response caching to avoid redundant API calls.
**Success metric**: Runner can execute scenarios against MockBackend, OpenAIBackend, AnthropicBackend.
**Estimated effort**: 4 hours
**Dependencies**: Prompt 1 (unified registry for backend plugins)

### Prompt 3: CLI Config Validation Command (B3)
**Trigger**: When users need to validate harness configs before running expensive evaluations.
**Action**: Add `harness validate <config.yaml>` command that loads config and runs validate_detailed(), printing structured errors and warnings. Add `--strict` flag to treat warnings as errors.
**Success metric**: Invalid configs are caught before any scenarios run; valid configs show green checkmark.
**Estimated effort**: 1 hour
**Dependencies**: Bundle 2 (B2 already adds validate_detailed)

### Prompt 4: Config Snapshot Versioning (C2)
**Trigger**: When you need content-addressed config storage for reproducibility.
**Action**: Add content-addressed storage to HarnessLineage using hash_dict() as the key. Store config snapshots in a content-addressed file tree (`.harness_lineage/snapshots/<hash>/`). Deduplicate identical configs automatically.
**Success metric**: Identical configs share storage; checkout by hash works; history is fully reproducible.
**Estimated effort**: 2 hours
**Dependencies**: Bundle 1 (lineage already exists)

### Prompt 5: Export Pipeline — Parquet + OTel (C3)
**Trigger**: When you need to export traces to external systems (data warehouse, observability platform).
**Action**: Add `TraceStore.export_parquet(path)` using pyarrow, and `export_otel()` producing OpenTelemetry-compatible JSON. Add `TraceStore.export()` dispatcher that selects format based on file extension.
**Success metric**: Traces export to .parquet, .csv, .json, and OTel JSON formats.
**Estimated effort**: 3 hours
**Dependencies**: Bundle 3 (C1 analytics already exists)

### Prompt 6: Connection Pooling + WAL Optimization (D1)
**Trigger**: When TraceStore becomes a bottleneck under concurrent access.
**Action**: Replace per-TraceStore connections with a connection pool (sqlite3 + queue.Queue). Optimize WAL mode settings (PRAGMA journal_size_limit, PRAGMA mmap_size). Add connection health checks.
**Success metric**: Concurrent reads/writes scale to 100+ threads without "database is locked" errors.
**Estimated effort**: 3 hours
**Dependencies**: Bundle 3 (TraceStore already has WAL mode)

### Prompt 7: Async DataGateway with aiohttp (D2)
**Trigger**: When DataGateway needs to make real HTTP requests concurrently.
**Action**: Implement aiohttp-based DataGateway.request() with proper async session management, connection pooling, retry logic with exponential backoff, and circuit breaker pattern. Keep mock fallback for testing.
**Success metric**: 100 concurrent requests to different sources complete without blocking.
**Estimated effort**: 4 hours
**Dependencies**: Bundle 3 (DataGateway already has token bucket)

### Prompt 8: Immutable Audit Logging (E3)
**Trigger**: When compliance requires tamper-evident audit trails.
**Action**: Add append-only audit log (JSONL) with cryptographic chaining (each entry includes hash of previous entry). Log all policy decisions, patch evaluations, and promotion state transitions. Add `AuditLog.verify_chain()` to detect tampering.
**Success metric**: Audit log is append-only; tampering detection works; entries are individually verifiable.
**Estimated effort**: 3 hours
**Dependencies**: Bundle 1 (PromotionPipeline + PolicyEngine already exist)

### Prompt 9: Health Check Endpoint (F2)
**Trigger**: When the harness runs as a service and needs monitoring.
**Action**: Add `harness health` CLI command and a `HealthChecker` class that probes all components (TraceStore, Lineage, PluginRegistry, DataGateway). Return structured health status with component-level pass/fail/warn.
**Success metric**: `harness health` returns 0 if all components healthy, 1 if any degraded, 2 if any critical.
**Estimated effort**: 2 hours
**Dependencies**: Bundle 3 (F1 signal handling already exists)

### Prompt 10: Web Dashboard Scaffold (G1)
**Trigger**: When users need a visual interface for lineage, traces, and results.
**Action**: Create a minimal FastAPI app (`src/harness/dashboard.py`) with endpoints: `/health`, `/lineage`, `/traces`, `/scenarios`, `/config`. Serve a simple HTML dashboard using Jinja2 templates with tables for traces and lineage visualization.
**Success metric**: `python -m harness.dashboard` starts a server; users can browse traces and lineage in a browser.
**Estimated effort**: 5 hours
**Dependencies**: Prompt 9 (health check), Prompt 5 (export pipeline)

### Prompt 11: MCP Adapter Skeleton (G2)
**Trigger**: When you need to bridge Harness surfaces to the MCP protocol.
**Action**: Create `src/harness/mcp_adapter.py` implementing the MCP server interface for the 17 harness surfaces. Each surface exposes tools/resources/prompts via MCP. Add `harness mcp` CLI command to start the MCP server.
**Success metric**: Claude Desktop / Cursor can connect to the harness MCP server and discover surfaces as tools.
**Estimated effort**: 5 hours
**Dependencies**: Prompt 1 (unified registry), Prompt 2 (AgentBackend integration)

---

## Recommended Execution Order

```
Phase 1 (Foundation): Prompt 1 → Prompt 3 → Prompt 4
Phase 2 (Integration): Prompt 2 → Prompt 11 → Prompt 9
Phase 3 (Scale): Prompt 6 → Prompt 7 → Prompt 5
Phase 4 (Compliance): Prompt 8
Phase 5 (Experience): Prompt 10
```

## Running the Harness

```bash
cd /mnt/agents/output/project

# Run all 569 tests
PYTHONPATH=src python -m pytest tests/ -v

# Run FinOps demo
PYTHONPATH=src python tests/demo/finops_demo.py

# CLI commands
PYTHONPATH=src python -m harness.cli run finops
PYTHONPATH=src python -m harness.cli evolve finops --max-cycles 5
PYTHONPATH=src python -m harness.cli validate config.yaml
PYTHONPATH=src python -m harness.cli variants finops --count 3
```
