# Changelog

All notable changes to the Harness Framework are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.2] - 2026-09-21 (Wave C — Governance Track)

### Added

#### T1.6 — Semantic Lifecycle Gates
- `lifecycle/semantic_hooks.py`: `SemanticHookBase` + three reflex-backed hooks
  (`SemanticDangerousCommandHook`, `SemanticSecretLeakHook`,
  `SemanticPatchRiskHook`) using `bool_gate` with block/review thresholds;
  fail-closed on backend errors; `register_semantic_gates()` opt-in —
  regex hooks fully preserved, chains coexist

#### T2.4 — Content-Addressed Snapshot Store
- `store/snapshots.py`: `ContentAddressedStore` — sha256-addressed artifact
  blobs (config + rubric), 2-char sharded tree, write-once dedupe with
  persistent `dedupe_hits`, JSONL index with tombstones, `verify()` corruption
  detection, thread-safe; lineage integration: `snapshot_hash` on nodes,
  `checkout_by_hash()`, `snapshot_rubric()`

#### T2.8 — Immutable Audit Log
- `audit.py`: append-only JSONL with SHA-256 chaining (`entry_hash =
  sha256(prev_hash + canonical)`); `verify_chain()` detects modification,
  deletion, reordering, truncation with typed failure reasons; corrupt-tail
  quarantine on open; `AuditRecorder` facade with policy/promotion/reflex/
  rubric action helpers (None-safe)

#### T1.5 — MetaRefinementAnalyzer (the loop closer)
- `reflex/meta.py`: decision-vs-outcome auditing — deterministic train/held-out
  split, per-rule precision/recall/FP-rate, sliding-window drift detection,
  `propose_patch()` emitting `HarnessPatch(surface="reflex")` with auto-inverse,
  `evaluate_patch()` on held-out logs with precision-improvement + recall-guard
  promotion criteria, `refine_rubric()` (FP-signal downweighting,
  required-signal promotion)
- Rubric schema v2: `Rubric.required_signals` (additive); MockReflexBackend
  honors it (rubric inapplicable → 0.0)
- First governed refinement shipped: `N_PLUS_ONE_RUBRIC` v2.0.0
  (`required_signals=["objects"]`, parent_version lineage) + rule-1
  logging-context scoping — no existing test modified (all TP behavior intact)

### Measured Impact
- Dogfood rescan: findings **32 → 8 (−75%)**; secret-leak FPs **−100%**;
  blocking-eligible **−100%** (R0→R1, `docs/reflex_baseline_r1.md`)
- Tests: 963 → **1120 passing** (+32 +43 +43 +39); exports: 131

## [0.4.1] - 2026-09-21 (Wave B — Deployment Track)

### Added

#### T1.4 — ReflexCIRunner CLI + Dogfood Activation
- `python3 -m harness.reflex` entry point: `--diff BASE HEAD` (real git diff
  parsing via `parse_git_diff`/`get_diff_snippets`), `--files`, `--phase
  {shadow,advisory,gating}` (env `REFLEX_AUTONOMY_PHASE`), `--summary`
  (GitHub Step Summary), `--json`, `--remediate`
- Phase-aware exit codes: shadow/advisory never block; gating blocks only on
  bool-invariant (secret) findings and OWASP severity > 7.0
- Dogfood default: scans `src/harness/**/*.py` (40-file cap) when no source given
- `.github/workflows/reflex_ci.yml` activated in advisory mode
- PEP 562 lazy ci imports in `reflex/__init__.py` (eliminates runpy warning)
- `docs/reflex_baseline_r0.md`: first dogfood measurement — 40 files, 32
  findings, est. precision ~28% under mock backend; rubric refinement backlog
  recorded for T1.5 meta-refinement

#### T2.5 — TraceStore Connection Pooling + WAL Optimization
- `src/harness/store/pool.py`: `SQLiteConnectionPool` (bounded, Queue-backed,
  FIFO-fair, health checks via `PRAGMA quick_check`, idempotent close) with 6
  tuned pragmas: WAL, `synchronous=NORMAL`, 64MB journal limit, 256MB mmap,
  64MB cache, configurable busy timeout
- TraceStore optional `pool=` integration (borrow-per-operation); zero change
  to default thread-local behavior or any existing signature/schema
- `optimize_storage()`: wal_checkpoint(TRUNCATE) + optimize + guarded VACUUM
  with before/after page stats
- Stress-validated: 100 threads × 20 records → 2000/2000, zero "database is
  locked", ~5,377 records/sec; 50 readers + 20 writers clean

#### T2.3 — CLI `harness validate`
- `harness validate <config.yaml> [--strict] [--json] [--no-color]`:
  validate_detailed() rendered as errors/warnings sections + config summary
- Exit codes: 0 valid / 1 errors (or warnings with --strict) / 2 load failure
- Machine-readable `--json` (dict shape, human output suppressed)
- Legacy `--harness-validate <config>` flag matching existing conventions

### Metrics
- Tests: 860 → **963 passing** (+42 T1.4, +41 T2.5, +20 T2.3)
- Known flake: `test_concurrent_readers_and_writers` is timing-sensitive under
  extreme reader pressure (passes on rerun; bounded by 30s busy timeout)

## [0.4.0] - 2026-09-21

### Added — The Reflex Layer (System 1 / System 2 Two-Tier Architecture)

Integrates the Jev primitives pattern (bool/score/choice zero-text classification)
as a first-class, governed harness tier. See REFLEX_PLAN.md for the full strategy.

#### Reflex Foundation (`src/harness/reflex/`)
- **ReflexBackend ABC**: System 1 classification backend contract — `bool_check`
  (probability), `score_check` (0–10 rubric), `choice_check` (≤255 options)
- **MockReflexBackend**: deterministic keyword-heuristic implementation for
  offline testing (mirrors MockBackend pattern)
- **JevBackend**: HTTP client for TypeSafe-Jev-compatible APIs with graceful
  mock fallback (no vendor lock-in above backend.py)
- **ReflexPrimitives / ReflexResult**: thin wrapper with escalation logic;
  every result carries value, confidence, latency_ms, and escalate flag
- **Rubric / RubricRegistry**: versioned qualitative rubrics as editable
  artifacts with lineage (parent_version, motivation) — the meta-refinement target

#### Reflex Integration
- **ReflexiveVerifier**: plugs rubric-scored verification into the existing
  verifier framework
- **QualitativeLinter**: 6-rule audit ported from the uploaded v3 prototype and
  rebuilt on primitives + rubrics — secret leakage (bool), function intent
  (score), comment quality (choice), diff pre-screen (score), N+1 ORM smell
  (score with 0–2/3–5/6–8/9–10 bands), OWASP scan (choice + severity score)
- **ReflexRouter**: System 1 skill/tool routing (choice over ≤255 skills) with
  token-savings estimation, ported from skill_routing_proxy
- **ReflexGate**: fast acceptance gate that pre-screens before expensive gates
- **EscalationPolicy**: confidence-floor / threshold handoff to System 2 with
  drift statistics (escalation rate, by-reason counts)
- **ReflexCIRunner**: closed-loop CI — scan → escalate → remediate → re-verify,
  ported from ci_runner-v2

#### Governance
- **19th surface**: `SurfaceType.REFLEX` — rubric artifacts become patchable,
  promotable, revertible via the existing HarnessPatch/PromotionPipeline
- **Phased autonomy** (R0 shadow → R1 advisory → R2 routing → R3 gating →
  R4 remediation → R5 meta-refinement) with KPI promotion criteria and
  auto-demotion (REFLEX_PLAN.md §6)
- **`.github/workflows/reflex_ci.yml`**: dogfood pipeline — this repo is the
  reference deployment, starting in advisory mode

#### Planning Documents
- **REFLEX_PLAN.md**: two-tier architecture, escalation contract, rubrics-as-
  governed-artifacts innovation, re-tiered roadmap (T1/T2 tracks, waves A–E),
  KPI framework, risk register
- **ROADMAP.md**: re-tiered into Tier 1 (reflex rollout T1.1–T1.7) and Tier 2
  (existing prompts T2.1–T2.11) with dependency-ordered execution waves

### Changed
- ROADMAP.md: 11 original prompts preserved but superseded in ordering by
  T1/T2 tracks
- PROJECT_CONTEXT.md: Session 7 logged

## [0.3.0] - 2026-08-19

### Added

#### Knowledge Graph Surface (18th Surface)
- **Graph Pipeline**: 4-stage pipeline (Extract → Resolve → Assemble → Query) following Anthropic's Graph-Engineering Playbook
- **GraphExtractor**: Entity and relation extraction from documents with MockBackend compatibility
- **EntityResolver**: Multi-signal clustering (exact, case-insensitive, substring, Jaccard similarity) for surface-form resolution
- **GraphAssembler**: NetworkX MultiDiGraph construction with pure-Python fallback
- **GraphQuerier**: Subgraph serialization, multi-hop path finding, neighbor queries with citation support
- **ProvenanceTracker**: Source document, timestamp, agent ID, and confidence tracking for every graph element
- **KnowledgeGraphPipeline**: Full pipeline orchestration with `build()` and `query()` methods
- **KnowledgeGraphPlugin**: Harness plugin integration for the knowledge_graph surface
- **Apollo Corpus Tests**: 6-document Apollo 11 corpus validates multi-hop reasoning ("Who walked on the Moon?")
- **67 tests** covering extraction, resolution, assembly, querying, provenance, and full pipeline integration

#### Swarm Orchestrator
- **SwarmCoordinator**: Dynamic agent swarm with task decomposition, work-stealing, and consensus aggregation
- **WorkStealingQueue**: Thread-safe Chase-Lev style work-stealing for load balancing
- **SwarmTask**: Task delegation with priorities, dependencies, and cost estimates
- **SwarmWorker**: Role-based workers (coder, explorer, planner, synthesizer, verifier) with cost tracking
- **ConsensusReport**: Agreement scoring with consensus output and dissenting view identification
- **Dynamic Decomposition**: Rule-based task splitting into parallel subtasks
- **Early Termination**: Stop execution when consensus threshold is reached (20-40% cost savings)
- **Cost Budget Enforcement**: Per-run spending limits with `BudgetExceededError`
- **Dependency Ordering**: Wave-based execution respecting task prerequisites
- **SwarmOrchestrationPlugin**: Harness plugin integration
- **63 tests** covering decomposition, work-stealing, consensus, budget, timeout, and error handling

#### Lifecycle Hooks + Approval Modes
- **LifecycleManager**: Chain-of-responsibility hook registration and execution
- **LifecycleHook ABC**: Base class for pre/post execution hooks with priority ordering
- **HookPoint Enum**: 10 lifecycle points (PRE_TOOL_CALL, POST_TOOL_CALL, PRE_PROPOSAL, etc.)
- **ApprovalMode Enum**: AUTO, CONFIRM, SIMULATE, NEVER, REJECT
- **ApprovalManager**: Per-tool and per-surface approval configuration
- **DangerousCommandHook**: Blocks rm -rf, curl | sh, sudo, chmod 777, and other dangerous patterns
- **FileWriteApprovalHook**: Gates file writes with configurable auto-approve paths
- **AuditLogHook**: Records all tool calls to tamper-evident audit trail
- **CostBudgetHook**: Enforces per-scenario cost budgets at hook level
- **InstrumentedTool**: Wrapper that adds PRE/POST hooks around any Tool
- **59 tests** covering chain execution, abort, context modification, thread safety, and integration

#### Core Infrastructure
- `KNOWLEDGE_GRAPH` added to `SurfaceType` enum
- **97 exported symbols** (up from 67)
- **758 total tests** (up from 569)
- **~14,600 source lines** across 61 source files

### Changed

- **Plugin system**: Converted `plugins.py` module to `plugins/` package for extensibility
- **__init__.py exports**: Added all new graph, swarm, and lifecycle symbols
- **Test structure**: Added 3 new test files (`test_knowledge_graph.py`, `test_swarm.py`, `test_lifecycle.py`)

### Fixed

- Missing `import copy` in `circuit_runner.py`
- Mutable class-level defaults in `Scenario` ABC (shared-state footgun)
- CLI exit code now returns 1 when scenarios fail

### Design Decisions

- **MockBackend compatibility**: All graph and swarm functionality works with deterministic mock backend for testing
- **NetworkX optional**: Pure-Python `_FallbackMultiDiGraph` when networkx is unavailable
- **Thread safety**: All swarm and lifecycle components use proper locking (`RLock`, `Event`)
- **Chain-of-responsibility**: Lifecycle hooks can modify context or abort chain; lower priority = earlier execution
- **Work-stealing**: Idle workers steal from busiest worker's tail, not random workers

## [0.2.0] - 2026-08-18

### Added

#### Governance Layer
- **AcceptanceSuite**: 8 mandatory acceptance gates (Regression, DiffScope, Security, Traceability, Rollback, Cost, Determinism)
- **PromotionPipeline**: PROPOSED → EVALUATED → QUARANTINED → ACCEPTED | REJECTED | REVERTED
- **PolicyEngine**: Edit permissions, privilege escalation detection, secret scanning, prompt safety
- **SelfHarnessLoop**: Propose → Evaluate → Decide cycle with held-in/held-out regression gates
- **TraceStore**: SQLite-backed with WAL mode, thread-local connections, JSON serialization
- **HarnessLineage**: Versioned configuration history with checkout/branch support

#### Plugin Architecture
- **Plugin ABC**: Base with `apply()`, `validate()`, `surface_name`
- **16 surface-specific plugins**: Identity, Instruction, Tool, Skill, MCP, Memory, Sandbox, Backend, Router, Orchestration, DataGateway, Verifier, Reporter, Artifact, Secrets, Policy
- **TrustLevel enum**: BUILTIN, VETTED, COMMUNITY, EXPERIMENTAL
- **PluginCapabilities**: Declared capabilities with version tracking

#### Agent Infrastructure
- **AgentBackend ABC**: MockBackend, OpenAIBackend stub, AnthropicBackend stub
- **BackendConfig**, **Message**, **BackendResponse**, **BackendCapability**
- **Tool system**: Tool ABC, ToolRegistry, ToolSchema
- **Sandboxed tools**: ReadFileTool, WriteFileTool, RunCommandTool with path traversal protection

#### CLI + UX
- **Rich CLI**: Progress bars, ASCII tables, ANSI colors, `--json` mode, `--no-color` flag
- **Config validation**: `validate_detailed()` with warnings for held_out_ratio, cost_budget
- **Config YAML export**: `to_yaml()` with serialization

#### Data + Security
- **TraceStore analytics**: Time-series bucketing, trend detection, anomaly detection (sigma threshold)
- **Secret scanning**: 10+ regex patterns, entropy checks, false-positive filtering
- **Sandbox hardening**: Null-byte detection, normalization, symlink checking
- **Signal handlers**: SIGINT/SIGTERM capture, handler registry, singleton pattern

### Metrics
- 569 tests passing
- ~10,800 source lines across 48 source files

## [0.1.0] - 2026-08-17

### Added

- **HarnessConfig**: Core configuration with surfaces, scenarios, verifiers
- **Surface / SurfaceType**: Testable system surface abstraction
- **Scenario ABC**: Setup, run, teardown, get_expected
- **Verifier ABC**: Exact, fuzzy, JSON schema verifiers
- **CircuitRunner**: ThreadPoolExecutor-based parallel evaluation
- **ScenarioSplit**: Train/test split for held-out evaluation
- **FailureClusterer**: Verifier-grounded signature extraction with Jaccard similarity
- **HarnessProposal**: Proposed change with parent_version, changes, proposer metadata
- **HarnessPatch**: Structured, invertible edit primitive (surface, target_id, operation, before/after, inverse)

### Design Foundations
- 17 declared surfaces
- Self-Harness loop concept (propose → evaluate → decide)
- Meta-Harness concept (external coding agents propose patches)
- Plugin architecture (base ABC + surface-specific ABCs)
- Promotion pipeline concept
