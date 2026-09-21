"""Tests for the harness.promotion module."""
from __future__ import annotations

import pytest

from harness.promotion import (
    PromotionPipeline,
    PromotionRecord,
    PromotionState,
)


# ---------------------------------------------------------------------------
# PromotionState enum
# ---------------------------------------------------------------------------

def test_promotion_state_values():
    """PromotionState enum has correct values."""
    assert PromotionState.PROPOSED.value == "proposed"
    assert PromotionState.EVALUATED.value == "evaluated"
    assert PromotionState.QUARANTINED.value == "quarantined"
    assert PromotionState.ACCEPTED.value == "accepted"
    assert PromotionState.REVERTED.value == "reverted"
    assert PromotionState.REJECTED.value == "rejected"


def test_promotion_state_members():
    """PromotionState has exactly 6 members."""
    assert len(PromotionState) == 6


# ---------------------------------------------------------------------------
# PromotionRecord creation
# ---------------------------------------------------------------------------

def test_promotion_record_creation():
    """PromotionRecord can be created with required fields."""
    record = PromotionRecord(
        promotion_id="promo-1",
        patch_id="patch-1",
        state=PromotionState.PROPOSED,
    )
    assert record.promotion_id == "promo-1"
    assert record.patch_id == "patch-1"
    assert record.state == PromotionState.PROPOSED
    assert record.state_history == []
    assert record.promoted_at is None
    assert record.reverted_at is None


def test_promotion_record_custom():
    """PromotionRecord can be fully customized."""
    record = PromotionRecord(
        promotion_id="promo-1",
        patch_id="patch-1",
        state=PromotionState.ACCEPTED,
        evaluation_scores={"score": 0.95},
        human_decision="approve",
        human_decider="alice",
        reason="All gates passed",
    )
    assert record.evaluation_scores == {"score": 0.95}
    assert record.human_decision == "approve"
    assert record.human_decider == "alice"
    assert record.reason == "All gates passed"


def test_promotion_record_str_state_coercion():
    """PromotionRecord coerces string state to enum."""
    record = PromotionRecord(
        promotion_id="promo-1",
        patch_id="patch-1",
        state="accepted",
    )
    assert record.state == PromotionState.ACCEPTED


def test_promotion_record_invalid_human_decision():
    """PromotionRecord normalizes invalid human_decision to None."""
    record = PromotionRecord(
        promotion_id="promo-1",
        patch_id="patch-1",
        state=PromotionState.PROPOSED,
        human_decision="maybe",
    )
    assert record.human_decision is None


# ---------------------------------------------------------------------------
# PromotionRecord.transition
# ---------------------------------------------------------------------------

def test_transition_records_history():
    """Each transition is recorded in state_history."""
    record = PromotionRecord(
        promotion_id="promo-1", patch_id="patch-1", state=PromotionState.PROPOSED
    )
    record.transition(PromotionState.EVALUATED, reason="Eval done")
    assert len(record.state_history) == 1
    assert record.state_history[0]["from"] == "proposed"
    assert record.state_history[0]["to"] == "evaluated"
    assert record.state_history[0]["reason"] == "Eval done"


def test_transition_to_accepted_sets_promoted_at():
    """Transition to ACCEPTED sets promoted_at."""
    record = PromotionRecord(
        promotion_id="promo-1", patch_id="patch-1", state=PromotionState.PROPOSED
    )
    record.transition(PromotionState.ACCEPTED)
    assert record.promoted_at is not None
    assert record.state == PromotionState.ACCEPTED


def test_transition_to_reverted_sets_reverted_at():
    """Transition to REVERTED sets reverted_at."""
    record = PromotionRecord(
        promotion_id="promo-1", patch_id="patch-1", state=PromotionState.ACCEPTED
    )
    record.transition(PromotionState.REVERTED)
    assert record.reverted_at is not None
    assert record.state == PromotionState.REVERTED


def test_is_terminal():
    """is_terminal returns True for terminal states."""
    for state in [PromotionState.ACCEPTED, PromotionState.REVERTED, PromotionState.REJECTED]:
        record = PromotionRecord(
            promotion_id="promo-1", patch_id="patch-1", state=state
        )
        assert record.is_terminal() is True

    record = PromotionRecord(
        promotion_id="promo-1", patch_id="patch-1", state=PromotionState.PROPOSED
    )
    assert record.is_terminal() is False


def test_to_dict():
    """PromotionRecord.to_dict() returns a dict."""
    record = PromotionRecord(
        promotion_id="promo-1", patch_id="patch-1", state=PromotionState.PROPOSED
    )
    d = record.to_dict()
    assert d["promotion_id"] == "promo-1"
    assert d["patch_id"] == "patch-1"
    assert d["state"] == "proposed"


# ---------------------------------------------------------------------------
# PromotionPipeline.submit()
# ---------------------------------------------------------------------------

def test_pipeline_submit():
    """submit creates a PROPOSED record."""
    pipeline = PromotionPipeline()
    record = pipeline.submit("patch-1", promotion_id="promo-1")
    assert record.state == PromotionState.PROPOSED
    assert record.patch_id == "patch-1"
    assert record.promotion_id == "promo-1"


def test_pipeline_submit_auto_id():
    """submit generates auto ID when not provided."""
    pipeline = PromotionPipeline()
    record = pipeline.submit("patch-1")
    assert record.promotion_id.startswith("promo-")


# ---------------------------------------------------------------------------
# evaluate()
# ---------------------------------------------------------------------------

def test_pipeline_evaluate():
    """evaluate transitions to EVALUATED."""
    pipeline = PromotionPipeline()
    record = pipeline.submit("patch-1", promotion_id="promo-1")
    result = pipeline.evaluate("promo-1", scores={"score": 0.9}, gate_reports=[])
    assert result.state == PromotionState.EVALUATED
    assert result.evaluation_scores == {"score": 0.9}
    assert len(result.state_history) == 1


def test_pipeline_evaluate_terminal_raises():
    """evaluate on terminal state raises ValueError."""
    pipeline = PromotionPipeline()
    record = pipeline.submit("patch-1", promotion_id="promo-1")
    pipeline.accept("promo-1")
    with pytest.raises(ValueError):
        pipeline.evaluate("promo-1", scores={}, gate_reports=[])


# ---------------------------------------------------------------------------
# quarantine()
# ---------------------------------------------------------------------------

def test_pipeline_quarantine():
    """quarantine transitions to QUARANTINED."""
    pipeline = PromotionPipeline()
    record = pipeline.submit("patch-1", promotion_id="promo-1")
    result = pipeline.quarantine("promo-1", reason="Security review")
    assert result.state == PromotionState.QUARANTINED
    assert len(result.state_history) == 1


# ---------------------------------------------------------------------------
# accept()
# ---------------------------------------------------------------------------

def test_pipeline_accept():
    """accept transitions to ACCEPTED and sets promoted_at."""
    pipeline = PromotionPipeline()
    record = pipeline.submit("patch-1", promotion_id="promo-1")
    result = pipeline.accept("promo-1")
    assert result.state == PromotionState.ACCEPTED
    assert result.promoted_at is not None


# ---------------------------------------------------------------------------
# reject()
# ---------------------------------------------------------------------------

def test_pipeline_reject():
    """reject transitions to REJECTED."""
    pipeline = PromotionPipeline()
    record = pipeline.submit("patch-1", promotion_id="promo-1")
    result = pipeline.reject("promo-1")
    assert result.state == PromotionState.REJECTED
    assert result.promoted_at is None


# ---------------------------------------------------------------------------
# revert()
# ---------------------------------------------------------------------------

def test_pipeline_revert():
    """revert transitions to REVERTED and sets reverted_at."""
    pipeline = PromotionPipeline()
    record = pipeline.submit("patch-1", promotion_id="promo-1")
    pipeline.accept("promo-1")
    result = pipeline.revert("promo-1")
    assert result.state == PromotionState.REVERTED
    assert result.reverted_at is not None


def test_pipeline_revert_not_accepted_raises():
    """revert on non-accepted state raises ValueError."""
    pipeline = PromotionPipeline()
    record = pipeline.submit("patch-1", promotion_id="promo-1")
    with pytest.raises(ValueError):
        pipeline.revert("promo-1")


# ---------------------------------------------------------------------------
# human_review()
# ---------------------------------------------------------------------------

def test_human_review_approve():
    """human_review with approve transitions to ACCEPTED."""
    pipeline = PromotionPipeline()
    record = pipeline.submit("patch-1", promotion_id="promo-1")
    result = pipeline.human_review("promo-1", decision="approve", decider="alice")
    assert result.state == PromotionState.ACCEPTED
    assert result.human_decision == "approve"
    assert result.human_decider == "alice"
    assert result.promoted_at is not None


def test_human_review_reject():
    """human_review with reject transitions to REJECTED."""
    pipeline = PromotionPipeline()
    record = pipeline.submit("patch-1", promotion_id="promo-1")
    result = pipeline.human_review("promo-1", decision="reject", decider="bob", reason="Issues found")
    assert result.state == PromotionState.REJECTED
    assert result.human_decision == "reject"


def test_human_review_invalid_decision():
    """human_review with invalid decision raises ValueError."""
    pipeline = PromotionPipeline()
    record = pipeline.submit("patch-1", promotion_id="promo-1")
    with pytest.raises(ValueError, match="approve"):
        pipeline.human_review("promo-1", decision="maybe", decider="alice")


# ---------------------------------------------------------------------------
# list_by_state()
# ---------------------------------------------------------------------------

def test_list_by_state():
    """list_by_state filters correctly."""
    pipeline = PromotionPipeline()
    pipeline.submit("patch-1", promotion_id="promo-1")
    pipeline.submit("patch-2", promotion_id="promo-2")
    pipeline.accept("promo-2")

    proposed = pipeline.list_by_state(PromotionState.PROPOSED)
    accepted = pipeline.list_by_state(PromotionState.ACCEPTED)
    assert len(proposed) == 1
    assert len(accepted) == 1
    assert proposed[0].promotion_id == "promo-1"
    assert accepted[0].promotion_id == "promo-2"


def test_list_by_state_string():
    """list_by_state accepts string state."""
    pipeline = PromotionPipeline()
    pipeline.submit("patch-1", promotion_id="promo-1")
    proposed = pipeline.list_by_state("proposed")
    assert len(proposed) == 1


def test_list_by_state_empty():
    """list_by_state returns empty list when no matches."""
    pipeline = PromotionPipeline()
    pipeline.submit("patch-1", promotion_id="promo-1")
    result = pipeline.list_by_state(PromotionState.REJECTED)
    assert result == []


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def test_get():
    """get returns record by ID."""
    pipeline = PromotionPipeline()
    pipeline.submit("patch-1", promotion_id="promo-1")
    record = pipeline.get("promo-1")
    assert record is not None
    assert record.promotion_id == "promo-1"


def test_get_missing():
    """get returns None for missing promotion_id."""
    pipeline = PromotionPipeline()
    assert pipeline.get("nonexistent") is None


def test_list_all():
    """list_all returns all records."""
    pipeline = PromotionPipeline()
    pipeline.submit("patch-1", promotion_id="promo-1")
    pipeline.submit("patch-2", promotion_id="promo-2")
    assert len(pipeline.list_all()) == 2


def test_list_by_patch():
    """list_by_patch returns records for a given patch."""
    pipeline = PromotionPipeline()
    pipeline.submit("patch-1", promotion_id="promo-1")
    result = pipeline.list_by_patch("patch-1")
    assert len(result) == 1


def test_count_by_state():
    """count_by_state returns correct count."""
    pipeline = PromotionPipeline()
    pipeline.submit("patch-1", promotion_id="promo-1")
    assert pipeline.count_by_state(PromotionState.PROPOSED) == 1
    assert pipeline.count_by_state(PromotionState.ACCEPTED) == 0


def test_count_by_state_none():
    """count_by_state with None returns total count."""
    pipeline = PromotionPipeline()
    pipeline.submit("patch-1", promotion_id="promo-1")
    pipeline.submit("patch-2", promotion_id="promo-2")
    assert pipeline.count_by_state() == 2


# ---------------------------------------------------------------------------
# Contains and len
# ---------------------------------------------------------------------------

def test_pipeline_contains():
    """Pipeline supports 'in' operator."""
    pipeline = PromotionPipeline()
    pipeline.submit("patch-1", promotion_id="promo-1")
    assert "promo-1" in pipeline
    assert "nonexistent" not in pipeline


def test_pipeline_len():
    """Pipeline supports len()."""
    pipeline = PromotionPipeline()
    assert len(pipeline) == 0
    pipeline.submit("patch-1", promotion_id="promo-1")
    assert len(pipeline) == 1


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_missing_promotion_id_raises_keyerror():
    """Accessing missing promotion_id raises KeyError."""
    pipeline = PromotionPipeline()
    with pytest.raises(KeyError, match="not found"):
        pipeline.accept("nonexistent")


# ---------------------------------------------------------------------------
# Delete and clear
# ---------------------------------------------------------------------------

def test_delete():
    """delete removes a record."""
    pipeline = PromotionPipeline()
    pipeline.submit("patch-1", promotion_id="promo-1")
    assert pipeline.delete("promo-1") is True
    assert pipeline.get("promo-1") is None


def test_delete_missing():
    """delete returns False for missing record."""
    pipeline = PromotionPipeline()
    assert pipeline.delete("nonexistent") is False


def test_clear():
    """clear removes all records."""
    pipeline = PromotionPipeline()
    pipeline.submit("patch-1", promotion_id="promo-1")
    pipeline.submit("patch-2", promotion_id="promo-2")
    pipeline.clear()
    assert len(pipeline) == 0


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def test_export_all():
    """export_all returns all records as dicts."""
    pipeline = PromotionPipeline()
    pipeline.submit("patch-1", promotion_id="promo-1")
    exported = pipeline.export_all()
    assert len(exported) == 1
    assert exported[0]["promotion_id"] == "promo-1"


def test_export_by_state():
    """export_by_state returns records in specific state."""
    pipeline = PromotionPipeline()
    pipeline.submit("patch-1", promotion_id="promo-1")
    exported = pipeline.export_by_state(PromotionState.PROPOSED)
    assert len(exported) == 1
