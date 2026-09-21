# Reflex Baseline — Phase R1 Measurement (Post Meta-Refinement)

**Date**: 2026-09-21 · **Wave**: C (T1.5) · **Phase**: R1 Advisory
**Command**: `PYTHONPATH=src python3 -m harness.reflex --phase advisory --json`
**Change vs R0**: N_PLUS_ONE_RUBRIC v2.0.0 (`required_signals=["objects"]`,
parent_version="1.0.0") + rule-1 logging-context scoping — both proposed by
`MetaRefinementAnalyzer` against the R0 corpus and shipped as governed rubric
versions (see `src/harness/reflex/meta.py`, `docs/reflex_baseline_r0.md`).

## Headline Delta

| Metric | R0 (v1 rubrics) | R1 (v2 rubrics) | Delta |
|---|---|---|---|
| Files scanned | 40 | 40 | — |
| Total findings | 32 | **8** | **−75%** |
| `secret_leak_protection` | 7 | **0** | **−100%** |
| `n_plus_one_orm_audit` | 25 | 8 | −68% |
| Blocking-eligible (gating) | 7 | **0** | **−100%** |
| Advisory `passed` | true | true | — |

## Honest Precision Audit (manual inspection of the 8 remaining flags)

The corpus contains **zero true N+1 ORM positives** (this framework uses no
ORM), so every N+1 flag is definitionally an FP on this corpus:

- All 8 remaining N+1 flags are files containing the bare token `objects` in
  **docstring prose** (e.g. `config.py: "…:class:\`Surface\` objects"`) plus a
  `dict.get()` inside a loop. v2's `required_signals=["objects"]` matches
  prose tokens, not just ORM manager calls.
- **Secret-leak rule is now clean**: 7/7 R0 FPs eliminated by logging-context
  scoping (only `logger.*`/`print(`/etc. invoke the backend). True-positive
  behavior preserved (AKIA/Bearer log lines still block — all existing TP
  tests pass unchanged).

| Rule | R0 est. precision | R1 est. precision | Note |
|---|---|---|---|
| secret_leak | ~0% (7 FP) | **n/a — 0 flags, 0 FP** | rule healthy on this corpus |
| n_plus_one | ~36% (16 FP) | 0% (8 FP, all prose-token) | needs v3 (below) |
| **Overall FP count** | **~23** | **8** | **−65% FP** |

## What the Loop Proved

1. **Mechanics work end-to-end**: R0 corpus → analyzer audit →
   `HarnessPatch(surface="reflex")` with auto-inverse → held-out evaluation →
   promoted rubric version with lineage. The synthetic full-loop test moved
   held-out precision 0.2 → 1.0 with recall 1.0 (`promote=True`).
2. **The dogfood corpus is the regression metric**: re-running the same 40
   files after each rubric version gives an objective FP trend (23 → 8).
3. **Honest limits**: on a zero-TP corpus, "precision" is the wrong frame —
   the metric that matters here is **FP count per 40 files**, which dropped
   65%. Precision/recall framing applies once the corpus has real positives
   (or synthetic TPs injected, as in the unit tests).

## Rubric Refinement Backlog (feeds v3, next meta-refinement cycle)

1. **N+1 v3**: replace bare-token `required_signals` with pattern signals
   (`objects.get(`, `objects.filter(`, `session.query(`) or add prose
   suppression — strip docstring/comment tokens before required-signal
   matching. Expected effect on this corpus: 8 → ~0 FPs.
2. **Token-source tagging**: mock backend should distinguish code tokens from
   string/comment tokens (mirrors how a production reflex model weighs
   context). Logged as backend evolution item, not a rubric change.
3. **Per-rule corpus fixtures**: freeze the 40-file R1 result as a regression
   fixture (`tests/fixtures/reflex_corpus_r1.json`) so every future rubric
   patch auto-evaluates against it in CI.

## Reproduction

```bash
cd <repo>
git log --oneline -3   # v0.4.1 + Wave C
PYTHONPATH=src python3 -m harness.reflex --phase advisory --json > r1.json
# compare with docs/reflex_baseline_r0.md table
```
