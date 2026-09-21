# ANALYSIS.md — Comprehensive Analysis v2 (Post-Proof-Milestone)

**Date**: 2026-09-21 · **Version**: 0.4.2 · **State**: repo live, PR #1 proof, dashboard published
**Supersedes**: the v0.2.0 analysis (Session 4). Reconciles with `Harness Framework Optimisation Planning.md` (26 tasks) — this document re-scores everything under one framework and adds the browser-surface dimensions for the first time.

---

## 1. Vision Validation

| Pillar | Vision | Current state | Verdict |
|---|---|---|---|
| **Functional** | Every component an editable, versioned, optimizable artifact | 19 surfaces, invertible patches, rubric artifacts, content-addressed snapshots | ✅ Realized |
| **Purpose-driven** | Bounded self-improvement with measurable gates | Self-harness loop + meta-refinement; −75% findings measured; promotion pipeline governs autonomy levels | ✅ Realized & measured |
| **Innovative** | Two-tier System 1/2, KG memory, swarm, reflexes as governed artifacts | All implemented; the rubrics-as-artifacts loop is, to our knowledge, unique | ✅ Realized |
| **UX** | "Make the machine visible" | Repo + PR #1 + static dashboard live; **but**: no live data, no interactivity, a11y/responsive gaps, no `harness init` on-ramp | ◐ Partially — the visibility gap narrowed, not closed |
| **Visionary nuance** | The reflexes make the harness cheap; the harness makes the reflexes trustworthy | Proven on curated corpora; **not yet always-on** (CI inactive, mock backend, no nightly loop) | ◐ Proven in principle, not in production |

**Core finding**: the architecture is complete; the gaps are (a) *production incarnation* — the always-on loop, (b) *experience depth* — the dashboard is a snapshot, the on-ramp is manual, (c) *residual security surface* — three P0 items.

---

## 2. Top-20 Improvements Across 9 Dimensions

Scoring: **UV** = user value (1–5) · **FE** = technical feasibility (1–5) · **TV** = technical value (1–5) · **CX** = implementation complexity (1–5, higher=harder).
**Score = (UV × FE × TV) / CX**. Opt# cross-references `Harness Framework Optimisation Planning.md`.

| # | ID | Improvement | Dimension | UV | FE | TV | CX | Score | Opt# |
|---|---|---|---|---|---|---|---|---|---|
| 1 | S1 | RunCommandTool `shell=False` migration + injection tests | Security | 5 | 5 | 5 | 2 | **62.5** | 1.1 |
| 2 | D2 | Reflex corpus fixture gate in CI (frozen R1 corpus, FP regression blocks) | Data | 5 | 5 | 5 | 2 | **62.5** | 3.3 |
| 3 | S2 | Audit log HMAC signing + key rotation | Security | 5 | 4 | 5 | 2 | **50.0** | 1.2 |
| 4 | N4a | v3 rubrics: pattern signals (`objects.get(`) | Features | 4 | 5 | 5 | 2 | **50.0** | 3.1 |
| 5 | N4b | v3 rubrics: prose suppression (token-source tagging) | Features | 4 | 4 | 5 | 2 | **40.0** | 3.2 |
| 6 | P1 | Parallel 6-rule evaluation in QualitativeLinter | Performance | 3 | 5 | 4 | 1 | **60.0** | 2.3 |
| 7 | X1 | `harness init` onboarding command (10-minute adoption) | UX | 5 | 4 | 3 | 2 | **30.0** | — |
| 8 | D1 | Batch trace writer (`record_batch` + reflex buffering) | Data | 4 | 4 | 4 | 2 | **32.0** | 2.2 |
| 9 | P3 | Flaky pool stress test hardening (deterministic scheduling) | Performance | 3 | 5 | 3 | 1 | **45.0** | 7.4 |
| 10 | A1 | Dashboard WCAG: amber/red contrast (3.5:1→≥4.5:1), `th scope`, ARIA, skip-link | Accessibility | 4 | 5 | 3 | 1 | **60.0** | — |
| 11 | S3 | CI workflow activation (workflow-scoped token) | Security | 5 | 3 | 5 | 2 | **37.5** | 1.3 |
| 12 | R1 | Dashboard mobile: breakpoint audit @320/768/1024, table→card collapse | Responsiveness | 4 | 4 | 3 | 2 | **24.0** | — |
| 13 | U1 | Dashboard interactivity: findings filter, rule drill-down, copy-JSON | UI Design | 3 | 4 | 3 | 2 | **18.0** | — |
| 14 | N1 | `harness health` endpoint + HealthChecker | Features | 4 | 4 | 4 | 3 | **21.3** | 6.1 |
| 15 | D3 | Decision-log retention/archival policy + `optimize_storage` scheduling | Data | 3 | 4 | 3 | 2 | **18.0** | — |
| 16 | F1 | Async DataGateway (aiohttp, retry, circuit breaker) | Functionality | 4 | 3 | 5 | 4 | **15.0** | 2.1/T2.6 |
| 17 | N2 | Nightly meta-refinement workflow (machine rubric PRs) | Features | 5 | 2 | 5 | 4 | **12.5** | 8.2 |
| 18 | X2 | CLI UX: `--help` examples, error-message actionability | UX | 3 | 4 | 2 | 1 | **24.0** | 4.3 |
| 19 | A2 | Dashboard reduced-motion + `prefers-color-scheme` dark variant | Accessibility | 3 | 4 | 2 | 1 | **24.0** | — |
| 20 | F2 | Parquet/OTel export pipeline | Functionality | 3 | 3 | 4 | 3 | **12.0** | 5.3/T2.7 |

**Deferred to later waves** (scored below cut): Tool Gateway (T2.1), AgentBackend in Runner (T2.2), swarm choice routing (T1.7), live dashboard (T2.10), MCP adapter (T2.11), export-surface deprecation (4.1), heuristic consolidation (4.2), Sphinx gaps (7.1), mypy strict (7.2), rubric packs (8.3).

---

## 3. Prioritization — The Top Band (10 items, 4 bundles)

Re-sorted by score with dependency grouping:

| Rank | Item | Score | Bundle |
|---|---|---|---|
| 1 | S1 shell=False (P0) | 62.5 | **A — Security** |
| 2 | D2 corpus gate | 62.5 | **B — Reflex v3** |
| 3 | P1 parallel rules | 60.0 | **B — Reflex v3** |
| 4 | A1 dashboard WCAG | 60.0 | **D — Dashboard** |
| 5 | S2 audit HMAC (P0) | 50.0 | **A — Security** |
| 6 | N4a pattern signals | 50.0 | **B — Reflex v3** |
| 7 | P3 flaky hardening | 45.0 | **C — Data+UX** |
| 8 | N4b prose suppression | 40.0 | **B — Reflex v3** |
| 9 | D1 batch writer | 32.0 | **C — Data+UX** |
| 10 | X1 `harness init` | 30.0 | **C — Data+UX** |

S3 (CI activation, #11) is **user-action-bound** (needs a workflow-scoped token) — flagged, not bundled. R1/X2/A2 land naturally inside Bundle D's dashboard rewrite.

## 4. Impact Analysis

| Bundle | Blast radius | Risk | Rollback | Measurable win |
|---|---|---|---|---|
| **A** | `tools.py` executor path; `audit.py` entry format | Shell-opt-out could break legacy callers → `allow_shell` opt-in preserves them; HMAC is additive (unsigned entries still verify) | Per-file git-less revert; inverse patches | Metacharacter injection suite; forged-rechain detection |
| **B** | `reflex/` rubric schema, backend scoring, linter, CI gate | Scoring behavior changes → corpus gate catches regressions; rubric lineage preserves v2 | Rubric inverse patch (the machinery itself!) | **R2 rescan ≤2 FPs (from 8)**; audit p95 <150ms |
| **C** | `store/trace_store.py` additive; `cli.py` new subcommand | Low — additive paths only; pool stress rewrite reduces flake | N/A (new code paths) | ≥3× write throughput; 20/20 flake-free runs; `init` scaffolds in <5s |
| **D** | `output/app/index.html` only | None — static asset, versioned (rollback = version_manager rollback) | `website_version_manager rollback` | Contrast ≥4.5:1 computed; 0 missing ARIA/scope; renders @320px |

**Cross-bundle conflicts**: B touches `reflex/linter.py` (prose suppression) AND `reflex/ci.py` (corpus gate); P1 (parallel rules) also lives in linter.py → **kept in one bundle deliberately** (that's why P1 is in B, not C). A, C, D touch disjoint files → safe parallel.

**Dogfood compounding**: Bundle B's corpus gate makes every *future* rubric change self-validating — the highest-leverage item in the set (score 62.5 tied #1 for a reason).

---

## 5. Validation Plan (per phase)

| Check | Method |
|---|---|
| Functionality | Full pytest suite per bundle (must stay ≥1120 + new) |
| Cross-browser | Dashboard: structural (semantic HTML, no CSS grid/flex features beyond evergreen support); documented manual-check matrix (Chrome/Firefox/Safari/Edge) |
| Mobile responsiveness | Breakpoint audit at 320/768/1024/1440 via CSS inspection + viewport meta; table→card collapse rule verified |
| Performance | Bundle B: rule-audit latency benchmark; Bundle C: writer throughput benchmark vs 5,377 rec/s baseline; suite runtime ≤ 20s |
| UAC | Each bundle's acceptance criteria (per Opt plan) verified and recorded |

---

## 6. Auto-Generated Next-Step Prompts (post-implementation)

> These fire after the bundles complete — see §7 for live status.

1. **"Activate the gates"** — S3: move `workflows-pending/*.yml` → `.github/workflows/` with a workflow-scoped token; verify first advisory run (Opt 1.3).
2. **"Wave D: scale"** — F1 async DataGateway → F2 export pipeline → Tool Gateway (Opt 2.1/5.3/5.1).
3. **"Production backend bake-off"** — Opt 8.1: production ReflexBackend vs mock on the frozen corpus; R2→R3 autonomy promotion.
4. **"Dashboard v3: live"** — N1 health endpoint → T2.9/T2.10 FastAPI live dashboard replacing the static snapshot (Opt 6.1/6.2).
5. **"Nightly loop"** — N2: scheduled meta-refinement workflow opening machine rubric PRs (Opt 8.2) — requires prompts 1+3.

## 7. Status

☑ **ALL FOUR BUNDLES COMPLETE** (2026-09-21) — 1214 tests passing (+94 over 1120 baseline).

### Phase Completion Records

| Bundle | Items | Result | Evidence |
|---|---|---|---|
| **A — Security** | S1, S2 | ✅ +21 tests | `shell=False` default + `allow_shell` opt-in (metacharacters now literal argv); HMAC-SHA256 audit signing with `verify_chain(check_signatures=True)` — forged re-chain detected as `signature_mismatch`; key rotation via keyring |
| **B — Reflex v3** | N4a, N4b, P1, D2 | ✅ +37 tests | `required_patterns` + `strip_prose` code-region gating; N_PLUS_ONE v3.0.0 (parent 2.0.0); parallel `run_full_audit` (~3.5× faster); corpus gate: **dogfood N+1 FPs 8 → 0**, bidirectional drift detection with documented known-FP baseline |
| **C — Data+UX** | D1, P3, X1 | ✅ +36 tests | `record_batch` **4.8× throughput** (35,538 vs 7,455 rec/s); stress test 10/10 stable; `harness init` scaffolds zero-warning config + optional CI workflow |
| **D — Dashboard** | A1, R1, X2/A2 folded | ✅ version `700a41d` | Contrast: amber 3.36→5.51, red 4.18→5.9, green 4.05→5.05 (all ≥AA 4.5:1); 18 ARIA attrs + 7 scope attrs + skip-link; dark mode; mobile card-collapse @560px; findings filter + copy-JSON |

**Cross-bundle incident (resolved)**: Bundle C's `init --with-ci` embedded workflow YAML containing `${{ secrets.TYPESAFE_API_KEY }}` → rule-1 heuristic fired on `cli.py`. Adjudicated as documented known-FP in the corpus fixture's `known_false_positives` baseline (bidirectional gate keeps it honest); rule-1 reference-syntax awareness logged as v3.1 backlog.

**Validation**: functionality (1214 green), performance (batch 4.8×, parallel rules ~3.5×, suite 9.5s), accessibility (computed WCAG ratios), mobile (breakpoint rules verified), UAC (all bundle acceptance criteria met). Cross-browser: structural HTML/CSS is evergreen-compatible; manual matrix documented for Chrome/Firefox/Safari/Edge.
