"""Tests for reflex meta-refinement (T1.5).

Covers the :class:`~harness.reflex.meta.MetaRefinementAnalyzer` loop
(ingest → metrics → drift → propose → evaluate → promote), rubric schema
v2 (``required_signals``), the R0 baseline rubric refinements in
:mod:`harness.reflex.linter`, and snapshot-store integration.

All tests use the deterministic :class:`MockReflexBackend`; no network.
"""

from __future__ import annotations

from typing import List

import pytest

from harness.patch import HarnessPatch, PatchOperation
from harness.reflex import MockReflexBackend, QualitativeLinter, ReflexPrimitives
from harness.reflex.backend import _tokenize
from harness.reflex.linter import N_PLUS_ONE_RUBRIC
from harness.reflex.meta import (
    DecisionRecord,
    MetaRefinementAnalyzer,
    RubricMetrics,
    rubric_from_dict,
    rubric_to_dict,
)
from harness.reflex.rubrics import Rubric, RubricRegistry
from harness.store.snapshots import ContentAddressedStore


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _record(
    decision_id: str,
    rule: str = "n_plus_one_orm",
    escalate: bool = True,
    outcome=None,
    snippet: str = "",
    value: float = 7.0,
) -> DecisionRecord:
    return DecisionRecord(
        decision_id=decision_id,
        rule=rule,
        value=value,
        confidence=0.9,
        escalate=escalate,
        snippet=snippet,
        outcome=outcome,
    )


def _ids_on_split(analyzer: MetaRefinementAnalyzer, prefix: str, held_out: bool, count: int) -> List[str]:
    """Deterministically find decision_ids landing on the requested split."""
    ids: List[str] = []
    candidate = 0
    while len(ids) < count:
        decision_id = f"{prefix}-{candidate:04d}"
        if analyzer._is_held_out(decision_id) is held_out:
            ids.append(decision_id)
        candidate += 1
    return ids


def _make_analyzer(rubric: Rubric, held_out_ratio: float = 0.2, snapshot_store=None):
    registry = RubricRegistry()
    registry.register(rubric)
    return MetaRefinementAnalyzer(
        registry, snapshot_store=snapshot_store, held_out_ratio=held_out_ratio
    )


N_PLUS_ONE_V1 = Rubric(
    rubric_id="n-plus-one-orm-v1",
    name="n_plus_one_orm",
    version="1.0.0",
    criteria=["9-10: explicit ORM queries inside loop bodies"],
    keyword_signals={
        "for ": 3.0,
        ".get(": 4.0,
        ".filter(": 3.0,
        "select_related": -10.0,
    },
    escalation_threshold=6.0,
    pass_threshold=2.0,
)

FP_SNIPPET = "for uid in ids:\n    user = cache.get(uid)"
TP_SNIPPET = "for uid in ids:\n    user = User.objects.get(id=uid)"


@pytest.fixture()
def backend() -> MockReflexBackend:
    return MockReflexBackend()


@pytest.fixture()
def linter(backend) -> QualitativeLinter:
    return QualitativeLinter(ReflexPrimitives(backend))


# ---------------------------------------------------------------------------
# 1. Ingest + deterministic train/held-out split
# ---------------------------------------------------------------------------


class TestIngestSplit:
    def test_same_decision_id_same_split_across_instances(self):
        records = [_record(f"dec-{i}", outcome=True) for i in range(40)]
        analyzer_a = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.25)
        analyzer_b = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.25)
        analyzer_a.ingest(records)
        analyzer_b.ingest(records)
        held_a = {r.decision_id for r in analyzer_a.held_out_records()}
        held_b = {r.decision_id for r in analyzer_b.held_out_records()}
        assert held_a == held_b
        assert held_a  # ratio 0.25 over 40 ids must yield some held-out

    def test_ratio_zero_puts_everything_in_train(self):
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.0)
        analyzer.ingest([_record(f"d-{i}", outcome=False) for i in range(10)])
        assert analyzer.held_out_records() == []
        assert len(analyzer.train_records()) == 10

    def test_ratio_one_puts_everything_in_held_out(self):
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=1.0)
        analyzer.ingest([_record(f"d-{i}", outcome=True) for i in range(10)])
        assert analyzer.train_records() == []
        assert len(analyzer.held_out_records()) == 10

    def test_ingest_rejects_non_records(self):
        analyzer = _make_analyzer(N_PLUS_ONE_V1)
        with pytest.raises(TypeError):
            analyzer.ingest([{"not": "a record"}])


# ---------------------------------------------------------------------------
# 2. metrics() precision/recall math
# ---------------------------------------------------------------------------


class TestMetrics:
    def _analyzer_with_known_records(self) -> MetaRefinementAnalyzer:
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.0)
        analyzer.ingest(
            [
                _record("tp", escalate=True, outcome=True),
                _record("fp", escalate=True, outcome=False),
                _record("fn", escalate=False, outcome=True),
                _record("tn", escalate=False, outcome=False),
                _record("unlabeled", escalate=True, outcome=None),
            ]
        )
        return analyzer

    def test_confusion_counts(self):
        metrics = self._analyzer_with_known_records().metrics()["n_plus_one_orm"]
        assert metrics.labeled == 4
        assert metrics.true_positives == 1
        assert metrics.false_positives == 1
        assert metrics.false_negatives == 1
        assert metrics.true_negatives == 1

    def test_precision_recall_fp_rate_math(self):
        metrics = self._analyzer_with_known_records().metrics()["n_plus_one_orm"]
        assert metrics.precision == pytest.approx(0.5)
        assert metrics.recall == pytest.approx(0.5)
        assert metrics.fp_rate == pytest.approx(0.5)

    def test_undefined_ratios_are_zero(self):
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.0)
        analyzer.ingest([_record("u1"), _record("u2")])  # all unlabeled
        metrics = analyzer.metrics()["n_plus_one_orm"]
        assert metrics.labeled == 0
        assert metrics.precision == 0.0
        assert metrics.recall == 0.0
        assert metrics.fp_rate == 0.0

    def test_metrics_uses_train_split_only_and_rule_filter(self):
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.3)
        train_ids = _ids_on_split(analyzer, "train", held_out=False, count=3)
        held_ids = _ids_on_split(analyzer, "held", held_out=True, count=2)
        analyzer.ingest([_record(i, escalate=True, outcome=False) for i in train_ids])
        analyzer.ingest([_record(i, escalate=True, outcome=True) for i in held_ids])
        analyzer.ingest([_record("other", rule="other_rule", escalate=True, outcome=True)])
        metrics = analyzer.metrics(rule="n_plus_one_orm")
        assert set(metrics) == {"n_plus_one_orm"}
        # Only the 3 train FPs are counted.
        assert metrics["n_plus_one_orm"].labeled == 3
        assert metrics["n_plus_one_orm"].false_positives == 3


# ---------------------------------------------------------------------------
# 3. detect_drift
# ---------------------------------------------------------------------------


class TestDetectDrift:
    def test_declining_precision_drifts(self):
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.0)
        records = [_record(f"early-{i}", escalate=True, outcome=True) for i in range(50)]
        records += [_record(f"late-{i}", escalate=True, outcome=False) for i in range(50)]
        analyzer.ingest(records)
        drift = analyzer.detect_drift("n_plus_one_orm", window=50)
        assert drift["drifting"] is True
        assert drift["slope"] < 0.0
        assert drift["windows"][0] == pytest.approx(1.0)
        assert drift["windows"][-1] == pytest.approx(0.0)

    def test_stable_precision_not_drifting(self):
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.0)
        records = [
            _record(f"alt-{i}", escalate=True, outcome=bool(i % 2)) for i in range(100)
        ]
        analyzer.ingest(records)
        drift = analyzer.detect_drift("n_plus_one_orm", window=50)
        assert drift["drifting"] is False
        assert drift["slope"] == pytest.approx(0.0, abs=1e-9)

    def test_insufficient_windows_not_drifting(self):
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.0)
        analyzer.ingest([_record(f"d-{i}", outcome=True) for i in range(10)])
        drift = analyzer.detect_drift("n_plus_one_orm", window=50)
        assert drift["drifting"] is False
        assert drift["slope"] == 0.0
        assert drift["windows"] == []


# ---------------------------------------------------------------------------
# 4. refine_rubric
# ---------------------------------------------------------------------------


class TestRefineRubric:
    def test_fp_driving_signal_is_halved(self):
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.0)
        rubric = Rubric(
            rubric_id="rx-v1",
            name="rx",
            version="1.0.0",
            keyword_signals={"xsink": 8.0, "keep": 2.0},
        )
        labeled = [
            _record(f"fp-{i}", rule="rx", escalate=True, outcome=False, snippet="xsink here")
            for i in range(4)
        ] + [
            _record(f"tp-{i}", rule="rx", escalate=True, outcome=True, snippet="keep calm")
            for i in range(4)
        ]
        refined = analyzer.refine_rubric(rubric, labeled)
        assert refined.keyword_signals["xsink"] == pytest.approx(4.0)
        assert refined.keyword_signals["keep"] == pytest.approx(2.0)

    def test_version_bumped_minor_with_parent(self):
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.0)
        refined = analyzer.refine_rubric(N_PLUS_ONE_V1, [])
        assert refined.version == "1.1.0"
        assert refined.parent_version == "1.0.0"
        assert refined.name == N_PLUS_ONE_V1.name

    def test_discriminating_token_promoted_to_required_signals(self):
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.0)
        labeled = [
            _record(f"fp-{i}", escalate=True, outcome=False, snippet=FP_SNIPPET)
            for i in range(6)
        ] + [
            _record(f"tp-{i}", escalate=True, outcome=True, snippet=TP_SNIPPET)
            for i in range(3)
        ]
        refined = analyzer.refine_rubric(N_PLUS_ONE_V1, labeled)
        assert "objects" in refined.required_signals
        assert "cache" not in refined.required_signals


# ---------------------------------------------------------------------------
# 5. propose_patch
# ---------------------------------------------------------------------------


class TestProposePatch:
    def _analyzer_with_fp_data(self) -> MetaRefinementAnalyzer:
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.0)
        analyzer.ingest(
            [_record(f"fp-{i}", escalate=True, outcome=False, snippet=FP_SNIPPET) for i in range(6)]
            + [_record(f"tp-{i}", escalate=True, outcome=True, snippet=TP_SNIPPET) for i in range(3)]
        )
        return analyzer

    def test_patch_shape(self):
        analyzer = self._analyzer_with_fp_data()
        patch = analyzer.propose_patch("n_plus_one_orm", "R0 baseline: kill dict.get FPs")
        assert isinstance(patch, HarnessPatch)
        assert patch.surface == "reflex"
        assert patch.target_id == "n_plus_one_orm"
        assert patch.operation is PatchOperation.REPLACE
        assert patch.motivation == "R0 baseline: kill dict.get FPs"
        assert patch.proposer_type == "meta"

    def test_patch_before_after_and_inverse(self):
        analyzer = self._analyzer_with_fp_data()
        patch = analyzer.propose_patch("n_plus_one_orm", "refine")
        assert patch.before["version"] == "1.0.0"
        assert patch.after["version"] == "1.1.0"
        assert patch.after["parent_version"] == "1.0.0"
        assert patch.after["required_signals"]  # refinement applied
        assert patch.inverse is not None
        assert patch.inverse.before == patch.after
        assert patch.inverse.after == patch.before
        # before/after round-trip into valid rubrics.
        assert rubric_from_dict(patch.before).version == "1.0.0"
        assert rubric_from_dict(patch.after).required_signals

    def test_none_when_no_improvement_available(self):
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.0)
        # Only confirmed findings: nothing to fix.
        analyzer.ingest(
            [_record(f"tp-{i}", escalate=True, outcome=True, snippet=TP_SNIPPET) for i in range(4)]
        )
        assert analyzer.propose_patch("n_plus_one_orm", "refine") is None

    def test_none_when_no_labeled_records(self):
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.0)
        analyzer.ingest([_record(f"u-{i}") for i in range(4)])
        assert analyzer.propose_patch("n_plus_one_orm", "refine") is None

    def test_none_when_rule_unknown(self):
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.0)
        assert analyzer.propose_patch("missing_rule", "refine") is None


# ---------------------------------------------------------------------------
# 6. evaluate_patch on held-out records
# ---------------------------------------------------------------------------


class TestEvaluatePatch:
    def _manual_patch(self, before: Rubric, after: Rubric, rule: str = "n_plus_one_orm") -> HarnessPatch:
        return HarnessPatch(
            patch_id="manual-test-patch",
            surface="reflex",
            target_id=rule,
            operation=PatchOperation.REPLACE,
            before=rubric_to_dict(before),
            after=rubric_to_dict(after),
            motivation="test",
        )

    def test_precision_improvement_promotes(self):
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.0)
        before = N_PLUS_ONE_V1
        after = analyzer.refine_rubric(
            N_PLUS_ONE_V1,
            [
                _record("fp", escalate=True, outcome=False, snippet=FP_SNIPPET),
                _record("tp", escalate=True, outcome=True, snippet=TP_SNIPPET),
            ],
        )
        patch = self._manual_patch(before, after)
        held_out = [
            _record("h-tp", escalate=True, outcome=True, snippet=TP_SNIPPET),
            _record("h-fp-1", escalate=True, outcome=False, snippet=FP_SNIPPET),
            _record("h-fp-2", escalate=True, outcome=False, snippet=FP_SNIPPET),
        ]
        result = analyzer.evaluate_patch(patch, held_out)
        assert result["before"].precision == pytest.approx(1 / 3, rel=1e-3)
        assert result["after"].precision == pytest.approx(1.0)
        assert result["precision_delta"] > 0.3
        assert result["fp_delta"] < 0.0
        assert result["promote"] is True

    def test_recall_regression_over_five_percent_blocks_promotion(self):
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.0)
        before = Rubric(
            rubric_id="rx-v1",
            name="n_plus_one_orm",
            version="1.0.0",
            keyword_signals={".get(": 8.0},
            escalation_threshold=6.0,
        )
        after = Rubric(
            rubric_id="rx-v2",
            name="n_plus_one_orm",
            version="1.1.0",
            keyword_signals={".get(": 8.0},
            escalation_threshold=6.0,
            required_signals=["objects"],
        )
        patch = self._manual_patch(before, after)
        tp_with_objects = "for uid in ids:\n    user = User.objects.get(id=uid)"
        tp_without_objects = "for uid in ids:\n    row = session.get(uid)"
        records = [
            _record("tp-1", escalate=True, outcome=True, snippet=tp_with_objects),
            _record("tp-2", escalate=True, outcome=True, snippet=tp_without_objects),
            _record("fp-1", escalate=True, outcome=False, snippet=FP_SNIPPET),
        ]
        result = analyzer.evaluate_patch(patch, records)
        assert result["after"].precision > result["before"].precision
        assert result["before"].recall == pytest.approx(1.0)
        assert result["after"].recall == pytest.approx(0.5)
        assert result["promote"] is False

    def test_empty_records_falls_back_to_held_out_split(self):
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.5)
        held_ids = _ids_on_split(analyzer, "held", held_out=True, count=2)
        analyzer.ingest(
            [_record(held_ids[0], escalate=True, outcome=True, snippet=TP_SNIPPET)]
            + [_record(held_ids[1], escalate=True, outcome=False, snippet=FP_SNIPPET)]
        )
        after = analyzer.refine_rubric(
            N_PLUS_ONE_V1,
            [_record("t-fp", escalate=True, outcome=False, snippet=FP_SNIPPET),
             _record("t-tp", escalate=True, outcome=True, snippet=TP_SNIPPET)],
        )
        patch = self._manual_patch(N_PLUS_ONE_V1, after)
        result = analyzer.evaluate_patch(patch, [])
        assert result["before"].labeled == 2
        assert result["promote"] is True


# ---------------------------------------------------------------------------
# 7. required_signals: rubric schema v2 + backend semantics
# ---------------------------------------------------------------------------


class TestRequiredSignals:
    def _rubric(self, required) -> Rubric:
        return Rubric(
            rubric_id="np1-test",
            name="n_plus_one_orm",
            keyword_signals={"for ": 3.0, ".get(": 4.0},
            escalation_threshold=6.0,
            required_signals=required,
        )

    def test_missing_required_signal_scores_zero(self, backend):
        score = backend.score_check(FP_SNIPPET, self._rubric(["objects"]))
        assert score == 0.0

    def test_present_required_signal_scores_normally(self, backend):
        score = backend.score_check(TP_SNIPPET, self._rubric(["objects"]))
        assert score == pytest.approx(7.0)

    def test_empty_required_signals_preserves_v1_behaviour(self, backend):
        score = backend.score_check(FP_SNIPPET, self._rubric([]))
        assert score == pytest.approx(7.0)

    def test_all_required_signals_must_be_present(self, backend):
        rubric = self._rubric(["objects", "session"])
        assert backend.score_check(TP_SNIPPET, rubric) == 0.0

    def test_registry_propose_update_carries_required_signals(self):
        registry = RubricRegistry()
        registry.register(self._rubric(["objects"]))
        updated = registry.propose_update("n_plus_one_orm", "tighten")
        assert updated.required_signals == ["objects"]
        overridden = registry.propose_update(
            "n_plus_one_orm", "override", required_signals=["session"]
        )
        assert overridden.required_signals == ["session"]

    def test_rubric_dict_roundtrip_preserves_required_signals(self):
        rubric = self._rubric(["objects"])
        restored = rubric_from_dict(rubric_to_dict(rubric))
        assert restored.required_signals == ["objects"]
        assert restored.version == rubric.version


# ---------------------------------------------------------------------------
# 8. Snapshot store integration
# ---------------------------------------------------------------------------


class TestSnapshotIntegration:
    def test_refined_rubric_dict_roundtrip(self, tmp_path):
        store = ContentAddressedStore(tmp_path / "snapshots")
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.0)
        refined = analyzer.refine_rubric(
            N_PLUS_ONE_V1,
            [
                _record("fp", escalate=True, outcome=False, snippet=FP_SNIPPET),
                _record("tp", escalate=True, outcome=True, snippet=TP_SNIPPET),
            ],
        )
        ref = store.put(rubric_to_dict(refined), artifact_type="rubric")
        loaded = store.get(ref.content_hash)
        assert store.has(ref.content_hash)
        assert loaded["version"] == refined.version
        assert loaded["parent_version"] == refined.parent_version
        assert loaded["required_signals"] == refined.required_signals
        assert store.verify(ref.content_hash) is True
        rubric_refs = store.list(artifact_type="rubric")
        assert any(r.content_hash == ref.content_hash for r in rubric_refs)

    def test_propose_patch_persists_after_dict(self, tmp_path):
        store = ContentAddressedStore(tmp_path / "snapshots")
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.0, snapshot_store=store)
        analyzer.ingest(
            [_record(f"fp-{i}", escalate=True, outcome=False, snippet=FP_SNIPPET) for i in range(4)]
            + [_record(f"tp-{i}", escalate=True, outcome=True, snippet=TP_SNIPPET) for i in range(2)]
        )
        patch = analyzer.propose_patch("n_plus_one_orm", "persist me")
        assert patch is not None
        rubric_refs = store.list(artifact_type="rubric")
        assert len(rubric_refs) == 1
        stored = store.get(rubric_refs[0].content_hash)
        assert stored == patch.after


# ---------------------------------------------------------------------------
# 9. Full loop: R0 baseline reproduction
# ---------------------------------------------------------------------------


class TestFullLoop:
    def test_r0_n_plus_one_refinement_promotes(self):
        """16 dict.get-style FPs + 3 ORM TPs → propose → evaluate → promote."""
        analyzer = _make_analyzer(N_PLUS_ONE_V1, held_out_ratio=0.2)
        fp_train = _ids_on_split(analyzer, "fp-train", held_out=False, count=12)
        fp_held = _ids_on_split(analyzer, "fp-held", held_out=True, count=4)
        tp_train = _ids_on_split(analyzer, "tp-train", held_out=False, count=2)
        tp_held = _ids_on_split(analyzer, "tp-held", held_out=True, count=1)
        analyzer.ingest(
            [_record(i, escalate=True, outcome=False, snippet=FP_SNIPPET) for i in fp_train + fp_held]
            + [_record(i, escalate=True, outcome=True, snippet=TP_SNIPPET) for i in tp_train + tp_held]
        )

        train_metrics = analyzer.metrics()["n_plus_one_orm"]
        assert train_metrics.precision == pytest.approx(2 / 14, rel=1e-3)

        patch = analyzer.propose_patch(
            "n_plus_one_orm", "R0 baseline: 16/25 N+1 findings were dict.get() FPs"
        )
        assert patch is not None
        assert "objects" in patch.after["required_signals"]

        result = analyzer.evaluate_patch(patch, analyzer.held_out_records("n_plus_one_orm"))
        assert result["before"].labeled == 5  # 4 FP + 1 TP held out
        assert result["before"].precision == pytest.approx(0.2)
        assert result["after"].precision == pytest.approx(1.0)
        assert result["after"].recall == pytest.approx(1.0)
        assert result["precision_delta"] > 0.3
        assert result["promote"] is True


# ---------------------------------------------------------------------------
# 10. R0 rubric refinements in the linter
# ---------------------------------------------------------------------------


class TestLinterR0Refinements:
    def test_n_plus_one_rubric_is_v2_with_lineage(self):
        assert N_PLUS_ONE_RUBRIC.version == "2.0.0"
        assert N_PLUS_ONE_RUBRIC.parent_version == "1.0.0"
        assert N_PLUS_ONE_RUBRIC.required_signals == ["objects"]
        assert N_PLUS_ONE_RUBRIC.name == "n_plus_one_orm"

    def test_dict_get_loop_no_longer_flagged(self, linter):
        result = linter.evaluate_n_plus_one_orm(
            "for user_id in user_ids:\n    user = cache.get(user_id)"
        )
        assert result.value == 0.0
        assert result.detail["status"] == "PASS"
        assert result.escalate is False

    def test_real_n_plus_one_still_flagged(self, linter):
        result = linter.evaluate_n_plus_one_orm(
            "orders = Order.objects.filter(status='PENDING')\n"
            "for order in orders:\n"
            "    user = User.objects.get(id=order.user_id)"
        )
        assert result.value > 6.0
        assert result.detail["status"] == "FLAG_N_PLUS_ONE_SMELL"

    def test_select_related_still_passes(self, linter):
        result = linter.evaluate_n_plus_one_orm(
            "orders = Order.objects.select_related('user').filter(status='PENDING')\n"
            "for order in orders:\n"
            "    print(order.user.email)"
        )
        assert result.value <= 6.0
        assert result.detail["status"] == "PASS"

    def test_docstring_token_passes_without_backend_call(self, linter, backend):
        before_calls = backend.call_count
        result = linter.evaluate_log_data_leakage(
            '"""Authentication helpers. The token is rotated hourly."""'
        )
        assert result.detail["action"] == "PASS"
        assert result.detail["skipped"] == "no_logging_context"
        assert result.escalate is False
        assert backend.call_count == before_calls  # backend never invoked

    def test_token_bucket_code_passes(self, linter):
        result = linter.evaluate_log_data_leakage(
            "def acquire(self):\n    self.tokens = max(0, self.tokens - 1)  # token bucket"
        )
        assert result.detail["action"] == "PASS"
        assert result.escalate is False

    def test_real_log_leak_still_blocked(self, linter):
        result = linter.evaluate_log_data_leakage(
            "logger.debug(f'token: {secret}')"
        )
        assert result.escalate is True
        assert result.detail["action"] == "BLOCK_PR_CRITICAL_LEAK"

    def test_print_leak_still_blocked(self, linter):
        result = linter.evaluate_log_data_leakage("print(f'password: {pw}')")
        assert result.escalate is True
        assert result.detail["action"] == "BLOCK_PR_CRITICAL_LEAK"
