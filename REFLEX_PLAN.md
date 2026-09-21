# REFLEX_PLAN.md — The Two-Tier Build Plan
## Refactoring the Harness Framework Around System 1 / System 2 Architecture

**Version**: 0.4.0-plan
**Date**: 2026-09-21
**Inputs**: Jev Primitives Handbook, Specification Paper (Reflexive AI Architectures for Autonomous Vulnerability Management), Strategic Implementation Roadmap, qualitative_linter v1–v3, ci_runner v1–v2, skill_routing_proxy

---

## 1. Executive Summary

The Harness Framework v0.3.0 is a complete **System 2** machine: deliberate scenario evaluation, LLM-backed verifiers, reasoning-heavy acceptance gates, and a self-improvement loop that proposes and judges patches. Every decision costs seconds and tokens.

The uploaded artifacts define the missing **System 1** tier: millisecond probabilistic reflexes — `bool` (binary invariant), `score` (0–10 rubric), `choice` (categorical routing) — that pre-screen, route, and gate *before* expensive reasoning is invoked. The uploads also ship working prototypes of this tier: a 6-rule qualitative linter, a closed-loop CI runner with auto-remediation, and a skill-routing proxy that cuts routing error from 17% to 7.3% while stripping ~10,000 tokens per turn.

This plan refactors the build roadmap so the reflex tier is not an add-on but a **first-class, governed harness layer**: reflex decisions feed TraceStore, reflex gates join the AcceptanceSuite, reflex routing becomes the concrete implementation of the `routing` surface, and — the central innovation — **rubrics become editable, versioned, patchable artifacts on a new 19th surface (`reflex`)**, so the existing Self-Harness machinery (patches, acceptance gates, promotion pipeline) governs the reflexes themselves. Meta-refinement is not a new loop; it is the loop we already built, pointed at rubric artifacts.

---

## 2. What the Uploads Bring (Artifact Inventory)

| Artifact | Content | Harness Reuse |
|---|---|---|
| Jev Primitives Handbook | `bool` / `score` / `choice` semantics, latency & cost envelopes (100–300ms, ~$0.00004/req), "smart switch statement" pattern, 6-step closed loop | Primitive contract for `ReflexBackend` ABC |
| Specification Paper | OWASP choice taxonomy, severity routing (>85% prob or >7.0 score → block), Sentry benchmark ($1.19 / 28M tokens), ci_runner-v2 execution logic | Threshold defaults, gate semantics, KPI targets |
| Strategic Implementation Roadmap | Phased rollout guidance (read-only first, then commit-back), Jev Proxy pattern, 4-stage meta-refinement pipeline, primitive selection matrix | Rollout discipline (§7), phased autonomy gates |
| `qualitative_linter.py` (v1) | 4 rules: secret leakage (bool), function intent (score), comment quality (choice), diff pre-screen (score) | Ported into `reflex/linter.py` |
| `qualitative_linter-v2.py` | + Rule 5: N+1 ORM smell (score, 0–2/3–5/6–8/9–10 bands) | Rubric definition for N+1 audit |
| `qualitative_linter-v3.py` | + Rule 6: OWASP scan (choice: SQLi/RCE/XSS/BAC + severity score) | Full 6-rule `QualitativeLinter` |
| `ci_runner.py` (v1) | PR diff extraction, 3-check pipeline, GitHub Step Summary markdown | `reflex/ci.py` scan phase |
| `ci_runner-v2.py` | + System 2 remediation handoff, auto-commit, instant re-verification | `reflex/ci.py` closed loop |
| `skill_routing_proxy.py` | `JevSkillRouter`: choice over ≤255 skills, token-savings estimator, HTTP + local fallback | `reflex/router.py` (`ReflexRouter`) |

**Prototype quality assessment**: the uploaded code is functional but monolithic (keyword logic inline, no rubric artifacts, no escalation policy, no governance hooks). The refactor extracts the *patterns* into harness-native modules: primitives behind a backend ABC, rules as versioned rubrics, decisions as traces, thresholds as policy.

---

## 3. The Strategic Gap: v0.3.0 Has No Reflexes

Every evaluation path in the current harness pays System 2 prices:

| Current path | Latency | Cost | What a reflex would do |
|---|---|---|---|
| `RegressionGate` re-runs scenarios | seconds | full scenario cost | `score` pre-screen: skip obviously-passing variants |
| `PolicyEngine.scan_for_secrets` regex | ms but brittle | free, high false-negatives | `bool` semantic check: "does this content expose a credential?" |
| Router surface | abstract, no implementation | — | `choice` over surfaces/skills, 7.3% error vs 17% |
| Lifecycle `DangerousCommandHook` regex | ms, brittle | free | `bool` semantic check with confidence + escalation |
| Verifier `LLMJudge` | seconds | $0.01–0.15/call | `score` rubric for the 80% of cases that are obvious |
| Swarm task assignment | rule-based decomposition | — | `choice` routes tasks to worker roles by capability |

The framework's own governance is exactly the kind of "millions of tokens" workload the Jev economics apply to: every patch proposal, every scenario run, every tool call is a candidate for a 100–300ms pre-screen that discards 90% of the work.

---

## 4. Target Architecture: Two Tiers, One Governance Spine

```
┌────────────────────────────────────────────────────────────────────┐
│  TIER 2 — System 2 (existing, deliberate)                          │
│  Scenarios · Verifiers · AcceptanceSuite · SelfHarnessLoop ·       │
│  SwarmCoordinator · KnowledgeGraphPipeline · Patch generation      │
│  Latency: seconds–minutes · Cost: $0.01–0.15+/decision             │
└───────────────▲────────────────────────────────────────────────────┘
                │ escalation: score ≥ 7.0 · p ≥ 0.85 · confidence < floor · novel pattern
                │ refinement: System 2 audits reflex decisions → rubric patches
┌───────────────┴────────────────────────────────────────────────────┐
│  TIER 1 — System 1 (NEW, reflexive)                                │
│  ReflexPrimitives (bool/score/choice) · QualitativeLinter (6) ·    │
│  ReflexRouter (≤255 skills) · ReflexGate · ReflexCIRunner ·        │
│  EscalationPolicy                                                  │
│  Latency: 100–300ms · Cost: ~$0.00004/decision                     │
└────────────────────────────────────────────────────────────────────┘
                │
┌───────────────┴────────────────────────────────────────────────────┐
│  GOVERNANCE SPINE (existing, shared by both tiers)                 │
│  TraceStore (every reflex decision = TraceRecord) · PolicyEngine · │
│  PromotionPipeline · HarnessLineage · 19 declared surfaces         │
└────────────────────────────────────────────────────────────────────┘
```

### 4.1 Surface Mapping — 18 → 19 Surfaces

| # | Surface | Reflex-layer role |
|---|---|---|
| 9 | `routing` | **Gains concrete implementation**: `ReflexRouter` (choice over skills/tools) |
| 12 | `evaluator` | **Gains reflexive tier**: `ReflexiveVerifier` + rubric-scored gates |
| 13 | `telemetry` | Every reflex decision recorded as `TraceRecord` (latency, confidence, escalate flag) |
| 16 | `policy_engine` | `bool` invariants become semantic fast-path before regex/rule checks |
| 10 | `orchestration` | Swarm task assignment via `choice` (task → worker role) |
| 13/16 | lifecycle hooks | `DangerousCommandHook`-style regex gates upgraded to `bool` semantic gates |
| **19** | **`reflex` (NEW)** | **Rubric artifacts**: versioned, patchable, promotable — the meta-refinement target |

### 4.2 New Module Layout

```
src/harness/reflex/
├── __init__.py
├── backend.py       # ReflexBackend ABC · MockReflexBackend (deterministic) · JevBackend (HTTP, graceful fallback)
├── primitives.py    # ReflexPrimitives · ReflexResult (value, confidence, latency, escalate)
├── rubrics.py       # Rubric (versioned artifact) · RubricRegistry (lineage)
├── escalation.py    # EscalationPolicy · EscalationEvent · drift stats
├── verifier.py      # ReflexiveVerifier (plugs into verifiers/)
├── linter.py        # QualitativeLinter (6 rules, rubric-driven)
├── router.py        # ReflexRouter · RoutableSkill · RoutingDecision
├── gates.py         # ReflexGate (plugs into AcceptanceSuite)
└── ci.py            # ReflexCIRunner (scan → escalate → remediate → re-verify)
```

Design invariants (mirroring existing harness patterns):

- **Deterministic testing**: `MockReflexBackend` — keyword heuristics, fixed latencies, zero network. The entire tier is testable offline, exactly like `MockBackend`.
- **Provider neutrality**: `JevBackend` speaks HTTP to a TypeSafe-compatible endpoint but degrades gracefully to mock; a future `LocalHeadBackend` (fine-tuned classifier head) slots into the same ABC. No vendor lock-in at the architecture level.
- **Escalation as contract**: every reflex result carries `confidence` and `escalate`. Low confidence is *not* a failure mode — it is the designed handoff to System 2. `EscalationPolicy` makes thresholds explicit, per-rule, and measurable.
- **Everything traced**: reflex decisions are `TraceRecord`s, so meta-refinement (§6) gets its audit corpus from infrastructure that already exists.

---

## 5. The Innovation: Rubrics as Governed Artifacts

The uploads describe meta-refinement — "System 2 audits Jev's low-confidence decisions and rewrites the rubrics" — as an external process. **The Harness already has the machinery to govern exactly this.** By declaring rubrics as artifacts on the `reflex` surface, meta-refinement becomes a standard self-improvement cycle:

```
1. OBSERVE   TraceStore accumulates reflex decisions
             (value, confidence, escalate, downstream System 2 verdict)
2. AUDIT     MetaRefinementAnalyzer correlates reflex decisions with
             System 2 outcomes → finds rubric false-positives/negatives
3. PROPOSE   Rubric change as HarnessPatch(surface="reflex",
             target_id="n_plus_one_orm", before=rubric_v3, after=rubric_v4)
             — with auto-computed inverse, like every patch
4. EVALUATE  Replay the rubric patch against held-out decision logs
             (the logs ARE the scenario corpus — no new eval infra needed)
5. GATE      AcceptanceSuite: regression (did FP/FN rates improve?),
             diff_scope (only rubric fields changed), rollback (inverse present)
6. PROMOTE   PROPOSED → EVALUATED → ACCEPTED — or REJECTED with evidence
```

This closes the deepest loop in the system: **the harness tunes its own reflexes using the same governed process it uses for everything else.** Nothing about meta-refinement is special-cased; it inherits inversion, lineage, quarantine, and rollback for free.

Held-out protection matters here more than anywhere: rubric patches are evaluated on a held-out slice of decision logs so a rubric cannot overfit to the decisions it was audited on — the same train/test discipline the harness already enforces for scenarios.

---

## 6. The Measured Part: Phased Autonomy with KPI Gates

The Strategic Roadmap upload is explicit: start read-only, earn autonomy. The rollout encodes that discipline — each phase transition requires hitting measurable criteria, and regression triggers automatic demotion (the promotion pipeline's REVERTED state applied to *autonomy levels*).

| Phase | Autonomy | What the reflex tier may do | Promotion criteria (measured) |
|---|---|---|---|
| **R0** | Shadow | Log decisions only; no effect on outcomes | Baseline established: ≥1,000 logged decisions per rule |
| **R1** | Advisory | Annotate PRs/traces with findings; humans act | Precision ≥ 90% on human-audited sample (n≥100/rule) |
| **R2** | Routing | `ReflexRouter` selects skills/tools for real | Routing error ≤ 10% (baseline 17%); fallback rate tracked |
| **R3** | Gating | `ReflexGate` blocks on `bool` invariants only | False-positive block rate < 2% over 2 weeks in advisory mode |
| **R4** | Remediation | Closed loop: scan → patch → re-verify in CI | Re-verification pass rate ≥ 95%; zero broken builds attributed to auto-fix |
| **R5** | Meta-refinement | Rubric patches auto-promoted through pipeline | Rubric patch acceptance judged on held-out logs; drift alarms armed |

Demotion rule: any phase breaching its criteria for 2 consecutive measurement windows drops one autonomy level and files an incident trace. Autonomy is a *promoted state*, not a configuration flag — it lives in the lineage, with history.

### KPI Framework (all emitted to TraceStore/FinOps)

| KPI | Source baseline | Target | Measurement |
|---|---|---|---|
| Reflex decision latency | 100–300ms | p95 < 300ms | TraceRecord latency field |
| Cost per audited PR | ~$0.10–1.00 (S2-only review) | < $0.01 | FinOps per-surface cost |
| Context forwarded to System 2 | 100% | ≤ 10% | payload bytes before/after pre-screen |
| Skill routing error rate | 17% standalone | ≤ 7.3–10% | sampled human/system-2 audit |
| Gate false-positive rate | n/a | < 2% | advisory-mode blocks overturned |
| Escalation rate | n/a | 5–15% (healthy band) | EscalationPolicy.stats() |
| Rubric drift | manual | automated weekly | decision-vs-outcome correlation trend |

---

## 7. Refactored Roadmap: Re-Tiering the Remaining Work

The 11 outstanding prompts from ROADMAP.md are re-sorted into the two tiers. Several System 2 items change character once the reflex tier exists (noted inline). New reflex items are **bolded**.

### Tier 1 Track — Reflex Rollout (new, this plan)

| # | Item | Effort | Depends on |
|---|---|---|---|
| **T1.1** | **`reflex/` foundation: backend ABC, primitives, rubrics, escalation** | 4h | — |
| **T1.2** | **QualitativeLinter (6 rules) + ReflexiveVerifier + ReflexGate** | 4h | T1.1 |
| **T1.3** | **ReflexRouter (skills ≤255) + token-savings telemetry** | 3h | T1.1 |
| **T1.4** | **ReflexCIRunner + GitHub Actions template (dogfood on this repo)** | 3h | T1.2 |
| **T1.5** | **MetaRefinementAnalyzer: decision-vs-outcome audit → rubric patch proposals** | 4h | T1.2 + T2.4 |
| **T1.6** | **Lifecycle hooks upgrade: regex gates → `bool` semantic gates (opt-in)** | 2h | T1.2 |
| **T1.7** | **Swarm task assignment via `choice` (task → worker role routing)** | 3h | T1.3 |

### Tier 2 Track — Existing Prompts, Re-Sequenced

| # | Item (from ROADMAP.md) | Effort | Change under two-tier plan |
|---|---|---|---|
| T2.1 | Tool Gateway (MCP unification) | 3h | Unchanged; becomes routable by T1.3 (tools as choice options) |
| T2.2 | AgentBackend integration into Runner | 4h | Unchanged |
| T2.3 | CLI `validate` command | 1h | Unchanged |
| T2.4 | Config snapshot versioning | 2h | Now also versions rubric artifacts (content-addressed) — feeds T1.5 |
| T2.5 | Connection pooling + WAL optimization | 3h | Higher priority: reflex tiers 10× the TraceStore write rate |
| T2.6 | Async DataGateway (aiohttp) | 4h | Unchanged |
| T2.7 | Export pipeline (Parquet + OTel) | 3h | Reflex decision logs are the first-class export corpus |
| T2.8 | Immutable audit logging | 3h | Must include reflex decisions + rubric patch lineage |
| T2.9 | Health check endpoint | 2h | Adds reflex backend liveness + escalation-rate probe |
| T2.10 | Web dashboard | 5h | First-class views: escalation queue, rubric drift, autonomy phase |
| T2.11 | MCP adapter | 5h | Router surface exposed as MCP tool; Jev-proxy pattern for MCP tool selection |

### Execution Order (dependency-respecting)

```
Wave A (foundation):        T1.1 → T1.2 → T1.3
Wave B (deployment):        T1.4 · T2.5 · T2.3        (parallel)
Wave C (governance):        T1.5 → T1.6 · T2.4 · T2.8 (parallel)
Wave D (scale):             T1.7 · T2.1 → T2.2 · T2.6 · T2.7
Wave E (experience):        T2.9 → T2.10 · T2.11
```

Each wave ends with the full test suite green plus its phase's KPI instrumentation verified — measurement is a merge requirement, not a follow-up.

---

## 8. Dogfooding: The Harness Guards Its Own Repository

The uploaded `ci_runner-v2.py` is, architecturally, "acceptance gates as a GitHub Action." Adopting it as this repository's own CI makes the framework's repo its reference deployment:

- **`.github/workflows/reflex_ci.yml`** (Wave B): reflex scan on every PR to the harness repo — secret leakage, OWASP scan, N+1 audit on our own diffs, GitHub Step Summary with probabilities and latencies.
- **Advisory first**: 2 weeks at Phase R1 (annotations only) to measure false-positive rate on ourselves before any blocking is enabled (R3).
- **Auto-remediation opt-in**: `[auto-fix]` commits only on explicitly labeled PRs during R4.
- **Self-referential validation**: the harness's own rubric patches (e.g., tuning the N+1 rubric against false positives found in our codebase) flow through the meta-refinement loop — the repo becomes the held-out corpus.

This turns every subsequent PR into evidence for or against the reflex tier — measurement is continuous, not a benchmark event.

---

## 9. Risk Register

| Risk | Severity | Mitigation |
|---|---|---|
| False-positive blocks erode developer trust | High | Phased autonomy (§6); advisory mode before every gating change; FP rate as promotion criterion |
| Rubric drift (reflex silently degrades) | High | Weekly decision-vs-outcome correlation; drift alarm at ρ < 0.7; auto-demotion |
| Prompt-injection via reflex inputs (natural-language criteria) | Medium | Criteria are developer-authored artifacts (versioned, reviewed) not runtime user input; PolicyEngine prompt-safety checks apply to rubric patches |
| Provider lock-in (TypeSafe/Jev API) | Medium | `ReflexBackend` ABC; mock + local-head backends; no Jev-specific types leak above `backend.py` |
| Reflex tier becomes a shadow governance path bypassing gates | Medium | Rubric changes are HarnessPatches through the *same* pipeline; autonomy levels are promoted states in lineage |
| Latency budget creep (reflex chain too slow) | Low | Per-decision latency in traces; p95 alarm at 300ms; parallel primitive evaluation where independent |
| 11-criteria rubric limit encourages criterion stuffing | Low | Rubric lint: warn at >8 criteria; prefer composing multiple rubrics |

---

## 10. What Changes in the Repo (Summary)

| Area | Change |
|---|---|
| `src/harness/reflex/` | NEW — 9 modules (backend, primitives, rubrics, escalation, verifier, linter, router, gates, ci) |
| `src/harness/core/types.py` | `SurfaceType.REFLEX` added (19th surface) |
| `src/harness/__init__.py` | +~20 exports (ReflexBackend, MockReflexBackend, JevBackend, ReflexPrimitives, ReflexResult, Rubric, RubricRegistry, EscalationPolicy, ReflexiveVerifier, QualitativeLinter, ReflexRouter, RoutableSkill, RoutingDecision, ReflexGate, ReflexCIRunner, AuditFinding) |
| `tests/test_reflex.py` | NEW — 55+ tests, mock-only |
| `.github/workflows/reflex_ci.yml` | NEW (Wave B) — dogfood pipeline, advisory mode |
| `ROADMAP.md` | Re-tiered into T1/T2 tracks (this plan, §7) |
| `PROJECT_CONTEXT.md` | Session 7 logged |
| Version | 0.3.0 → **0.4.0** on merge of Wave A |

---

## 11. Success Definition

The refactor is successful when, on this repository's own PRs:

1. A secret-leak or OWASP-critical diff is caught in **< 300ms for ~$0.00004**, before any System 2 call.
2. ≥ 90% of clean PRs never invoke System 2 review at all (10x context reduction realized, not projected).
3. Skill/tool routing error is measured and **≤ 10%** on a sampled audit.
4. A rubric change (e.g., N+1 threshold tuning) ships as a **HarnessPatch through the promotion pipeline**, evaluated on held-out decision logs — with a working inverse.
5. Every claim above is a TraceStore query away from verification.

That is the innovation and the measurement, in one loop: *the reflexes make the harness cheap; the harness makes the reflexes trustworthy.*
