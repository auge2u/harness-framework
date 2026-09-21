# Reflex Baseline — Phase R0/R1 Measurement (Dogfood)

**Date**: 2026-09-21 · **Wave**: B (T1.4) · **Phase**: R1 Advisory
**Command**: `PYTHONPATH=src python3 -m harness.reflex --phase advisory --json`
**Backend**: MockReflexBackend (deterministic keyword heuristics; no TYPESAFE_API_KEY set)

## Raw Results

| Metric | Value |
|---|---|
| Files scanned (src/harness/**/*.py, cap 40) | 40 |
| Total findings | 32 |
| `n_plus_one_orm_audit` | 25 (severity 7.0–10.0) |
| `secret_leak_protection` | 7 (severity 9.6) |
| Blocking-eligible (gating phase) | 7 |
| OWASP (`owasp_security_scan`) | 0 |

## False-Positive Estimate (manual/heuristic audit)

| Rule | Findings | Est. FP | Est. precision |
|---|---|---|---|
| N+1 ORM | 25 | ~16 (files with no ORM — `dict.get()`/`.filter()` in plain loops) | ~36% |
| Secret leak | 7 | ~7 ("token" in docstrings, token-bucket rate limiter, API-key env names) | ~0% |
| **Overall** | **32** | **~23** | **~28%** |

## Interpretation

This baseline is exactly what REFLEX_PLAN.md §6 predicts and guards against:

1. **Mock heuristics are not production reflexes.** The keyword-based
   MockReflexBackend exists for deterministic testing. Its FP rate on real
   code (~72% here) is why R3 gating requires a *production* backend
   (JevBackend/local head) and 2 advisory weeks with FP < 2%.
2. **Advisory mode is load-bearing.** Had this scan run in `gating` phase, 7
   PRs would have been wrongly blocked today. The phase ladder worked as
   designed: everything annotated, nothing blocked (`passed: true`).
3. **Rubric refinement backlog (feeds T1.5 meta-refinement):**
   - N+1 rubric: require an ORM signal (`*.objects.*`, `session.query`,
     Django/SQLAlchemy import) before loop+`.get(` scoring applies.
   - Secret rubric: restrict to logging contexts (`logger.*`, `print(`) and
     exclude benign identifiers (`token_bucket`, `csrf_token` names without
     values, docstring prose).
   - Add per-file-type suppression: `.py` docstrings/comments weighted lower.
4. **Baseline recorded for drift tracking.** Re-run this command after every
   rubric patch; precision on this fixed corpus is the regression metric for
   meta-refinement (target: ≥90% before R3).

## Reproduction

```bash
cd <repo>
PYTHONPATH=src python3 -m harness.reflex --phase advisory --json > r0.json
```

Raw artifact preserved at `/tmp/r0_scan.json` during the Wave B session;
summary markdown emitted via `--summary` flag (GitHub Step Summary format).
