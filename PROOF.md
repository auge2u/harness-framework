# Proof Milestone — The Loop, Visible

**Date**: 2026-09-21 · **Version**: 0.4.2 · **Status**: ✅ Complete

This document is the evidence index for the Harness Framework proof milestone:
the moment the system stopped being a claim ("1,120 tests pass, trust us") and
became something you can watch working.

---

## Evidence Artifacts

### 1. This Repository
The complete framework: 75 source files, 34 test files, 1,120 passing tests,
19 declared surfaces, 131 public exports.

```bash
git clone https://github.com/auge2u/harness-framework.git
cd harness-framework
pip install -e ".[dev]"
python -m pytest tests/ -q
```

### 2. PR #1 — The Closed Loop on a Real Pull Request
**https://github.com/auge2u/harness-framework/pull/1**

An intentionally flawed service (`demo/flawed_service.py`) carrying the three
finding classes. On that PR you can see:

- **Advisory scan comment**: the reflex linter flags all three findings in
  ~115ms each — secret leak (9.6), SQL injection (9.8), N+1 ORM (10.0) —
  posted as a table with escalation flags, in advisory phase (annotate,
  never block).
- **Closed-loop comment**: System 2 remediation patches all three; System 1
  re-verifies every patch clean (3/3).

Reproduce the scan against the exact PR file:

```bash
PYTHONPATH=src python3 -m harness.reflex --files demo/flawed_service.py --phase advisory --json
```

### 3. Proof Dashboard (live data, zero fabrication)
Rendered from the running system: the dogfood scan of this repo, the R0→R1
meta-refinement delta, and the hash-chained audit log of the proof loop
itself (7 entries, chain verified). Delivered as a versioned static page —
see the conversation version card (version ID `5aaa1dc`).

### 4. Measured Meta-Refinement (R0 → R1)
`docs/reflex_baseline_r0.md` and `docs/reflex_baseline_r1.md` in this repo:

| Metric | R0 (v1 rubrics) | R1 (v2 rubrics) | Delta |
|---|---|---|---|
| Findings (40-file dogfood) | 32 | 8 | **−75%** |
| Secret-leak false positives | 7 | 0 | **−100%** |
| Would-block (gating phase) | 7 | 0 | **−100%** |
| Held-out precision (synthetic corpus) | 0.188 | 1.000 | **+0.81** |

The R1 patch was proposed by `MetaRefinementAnalyzer` as a
`HarnessPatch(surface="reflex")` with auto-computed inverse, evaluated on
held-out decision logs (promotion guard: precision improves, recall
regression ≤ 5%), and shipped as rubric v2.0.0 with parent-version lineage.

### 5. Audit Chain
The proof loop's own governance trail — policy decision, reflex escalations,
promotion transitions, rubric update — as SHA-256-chained entries:
`verify_chain() → ok: true, entries_checked: 7`. Any edit, deletion, or
reorder breaks the chain (see `tests/test_audit.py` for the tamper matrix).

---

## What This Proves

1. **Reflexes work on real code**: a hosted PR scanned in milliseconds with
   actionable, escalating findings — advisory discipline intact.
2. **The loop closes**: scan → remediate → re-verify, 3/3 green.
3. **The system improves itself, measurably**: one governed rubric patch cut
   dogfood findings 75% with evidence preserved in-repo.
4. **Governance is real, not decorative**: every step above landed on the
   hash-chained audit log; rubric changes are invertible patches with lineage.

## Known Limits (honest)

- The CI workflows are staged at `.github/workflows-pending/` (publishing
  token lacked the `workflow` OAuth scope). One `git mv` activates them.
- Advisory findings under the deterministic mock backend are heuristics; the
  R1 baseline documents the remaining prose-token FPs and the v3 rubric
  backlog. Precision claims apply to the labeled corpora cited, not to
  arbitrary codebases.
- The dashboard is a static snapshot; a live TraceStore-backed dashboard is
  Wave E (T2.9/T2.10).

## Reproduction Checklist

```bash
# 1. Suite
python -m pytest tests/ -q                                    # 1120 passing

# 2. Dogfood scan of this repo (advisory)
PYTHONPATH=src python3 -m harness.reflex --phase advisory --json

# 3. The demo finding classes
PYTHONPATH=src python3 -m harness.reflex --files demo/flawed_service.py --phase advisory

# 4. Config validation
PYTHONPATH=src python -m harness.cli validate <your-config.yaml>
```
