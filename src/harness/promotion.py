"""Promotion state machine for harness patches.

States: PROPOSED -> EVALUATED -> QUARANTINED -> ACCEPTED | REVERTED

Each :class:`PromotionRecord` tracks the full lifecycle of a patch through the
promotion pipeline, including state transitions, evaluation scores, gate
reports, and optional human decisions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class PromotionState(Enum):
    """States in the patch promotion lifecycle."""

    PROPOSED = "proposed"
    EVALUATED = "evaluated"
    QUARANTINED = "quarantined"
    ACCEPTED = "accepted"
    REVERTED = "reverted"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# PromotionRecord
# ---------------------------------------------------------------------------


@dataclass
class PromotionRecord:
    """Record of a patch's journey through the promotion pipeline.

    Attributes
    ----------
    promotion_id:
        Unique identifier for this promotion record.
    patch_id:
        Identifier of the patch being promoted.
    state:
        Current promotion state.
    state_history:
        Chronological list of all state transitions, each as a dict with
        keys ``"from"``, ``"to"``, ``"at"`` (ISO timestamp), ``"reason"``,
        and ``"metadata"``.
    evaluation_scores:
        Dictionary of evaluation metrics (e.g., ``{"held_in_score": 0.92}``).
    gate_reports:
        Raw gate report dictionaries from the acceptance suite.
    human_decision:
        ``"approve"``, ``"reject"``, or ``None`` if no human has reviewed.
    human_decider:
        Identifier of the human who made the decision, or ``None``.
    created_at:
        Timestamp when the record was created.
    promoted_at:
        Timestamp when the patch was accepted, or ``None``.
    reverted_at:
        Timestamp when the patch was reverted, or ``None``.
    reason:
        Human-readable reason for the current state.
    """

    promotion_id: str
    patch_id: str
    state: PromotionState
    state_history: List[Dict[str, Any]] = field(default_factory=list)
    evaluation_scores: Dict[str, float] = field(default_factory=dict)
    gate_reports: List[Dict[str, Any]] = field(default_factory=list)
    human_decision: Optional[str] = None
    human_decider: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    promoted_at: Optional[datetime] = None
    reverted_at: Optional[datetime] = None
    reason: str = ""

    def __post_init__(self) -> None:
        """Normalise mutable defaults and coerce types after construction."""
        if self.state_history is None:
            self.state_history = []
        if self.evaluation_scores is None:
            self.evaluation_scores = {}
        if self.gate_reports is None:
            self.gate_reports = []
        if self.reason is None:
            self.reason = ""
        # Ensure state is a PromotionState enum.
        if isinstance(self.state, str):
            try:
                self.state = PromotionState(self.state.lower())
            except ValueError:
                self.state = PromotionState.PROPOSED
        # Validate human_decision.
        if self.human_decision is not None:
            self.human_decision = str(self.human_decision).lower().strip()
            if self.human_decision not in ("approve", "reject"):
                self.human_decision = None

    def transition(
        self,
        new_state: PromotionState,
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Transition to a new state, recording the history.

        Parameters
        ----------
        new_state:
            The state to transition into.
        reason:
            Human-readable explanation for the transition.
        metadata:
            Optional structured data to attach to the transition record.
        """
        if isinstance(new_state, str):
            new_state = PromotionState(new_state.lower())

        old_state = self.state
        self.state = new_state
        self.reason = reason or ""

        self.state_history.append(
            {
                "from": old_state.value,
                "to": new_state.value,
                "at": datetime.now(timezone.utc).isoformat(),
                "reason": reason or "",
                "metadata": dict(metadata) if metadata else {},
            }
        )

        if new_state == PromotionState.ACCEPTED:
            self.promoted_at = datetime.now(timezone.utc)
        elif new_state == PromotionState.REVERTED:
            self.reverted_at = datetime.now(timezone.utc)

    def is_terminal(self) -> bool:
        """Return ``True`` if the record is in a terminal state.

        Terminal states are :py:attr:`PromotionState.ACCEPTED`,
        :py:attr:`PromotionState.REVERTED`, and
        :py:attr:`PromotionState.REJECTED`.
        """
        return self.state in (
            PromotionState.ACCEPTED,
            PromotionState.REVERTED,
            PromotionState.REJECTED,
        )

    def elapsed_since_creation(self) -> Optional[float]:
        """Return the number of seconds since the record was created.

        Returns ``None`` if ``created_at`` is not set.
        """
        if self.created_at is None:
            return None
        delta = datetime.now(timezone.utc) - self.created_at
        return delta.total_seconds()

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this record to a plain dictionary.

        Useful for JSON logging, audit trails, and external integrations.
        """
        return {
            "promotion_id": self.promotion_id,
            "patch_id": self.patch_id,
            "state": self.state.value,
            "state_history": list(self.state_history),
            "evaluation_scores": dict(self.evaluation_scores),
            "gate_reports": list(self.gate_reports),
            "human_decision": self.human_decision,
            "human_decider": self.human_decider,
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
            "promoted_at": (
                self.promoted_at.isoformat() if self.promoted_at else None
            ),
            "reverted_at": (
                self.reverted_at.isoformat() if self.reverted_at else None
            ),
            "reason": self.reason,
        }

    def __repr__(self) -> str:
        return (
            f"PromotionRecord(id={self.promotion_id!r}, "
            f"patch={self.patch_id!r}, state={self.state.value})"
        )


# ---------------------------------------------------------------------------
# PromotionPipeline
# ---------------------------------------------------------------------------


class PromotionPipeline:
    """Manages the promotion lifecycle for patches.

    The pipeline maintains a registry of :class:`PromotionRecord` objects
    indexed by ``promotion_id``.  It provides methods for every state
    transition and supports human-in-the-loop review.

    Example::

        pipeline = PromotionPipeline()
        record = pipeline.submit("patch-123")
        record = pipeline.evaluate("promo-patch-123", scores={"score": 0.95}, gate_reports=[...])
        record = pipeline.accept("promo-patch-123")
    """

    def __init__(self) -> None:
        self._records: Dict[str, PromotionRecord] = {}

    # -- Lifecycle operations ------------------------------------------

    def submit(
        self, patch_id: str, promotion_id: Optional[str] = None
    ) -> PromotionRecord:
        """Submit a new patch for promotion.

        Creates a :class:`PromotionRecord` in the ``PROPOSED`` state.

        Parameters
        ----------
        patch_id:
            Identifier of the patch to promote.
        promotion_id:
            Optional custom promotion ID.  If ``None``, one is generated
            automatically.

        Returns
        -------
        PromotionRecord
            The newly created promotion record.
        """
        pid = promotion_id or f"promo-{patch_id}"
        record = PromotionRecord(
            promotion_id=pid,
            patch_id=patch_id,
            state=PromotionState.PROPOSED,
            reason="Patch submitted for promotion",
        )
        self._records[pid] = record
        return record

    def evaluate(
        self,
        promotion_id: str,
        scores: Dict[str, float],
        gate_reports: List[Dict[str, Any]],
    ) -> PromotionRecord:
        """Move a promotion to ``EVALUATED`` with scores and gate reports.

        Parameters
        ----------
        promotion_id:
            The promotion record identifier.
        scores:
            Dictionary of evaluation scores.
        gate_reports:
            List of gate report dictionaries from the acceptance suite.

        Returns
        -------
        PromotionRecord
            The updated promotion record.

        Raises
        ------
        KeyError
            If *promotion_id* is not found.
        ValueError
            If the record is not in a state that allows evaluation.
        """
        record = self._get(promotion_id)
        if record.is_terminal():
            raise ValueError(
                f"Cannot evaluate promotion '{promotion_id}' — "
                f"already in terminal state '{record.state.value}'"
            )
        record.evaluation_scores = dict(scores) if scores else {}
        record.gate_reports = list(gate_reports) if gate_reports else []
        record.transition(
            PromotionState.EVALUATED,
            "Evaluation completed with scores and gate reports",
            {"score_count": len(scores), "report_count": len(gate_reports)},
        )
        return record

    def quarantine(
        self, promotion_id: str, reason: str = ""
    ) -> PromotionRecord:
        """Move a promotion to ``QUARANTINED`` for human review.

        Parameters
        ----------
        promotion_id:
            The promotion record identifier.
        reason:
            Explanation for why the promotion was quarantined.

        Returns
        -------
        PromotionRecord
            The updated promotion record.

        Raises
        ------
        KeyError
            If *promotion_id* is not found.
        ValueError
            If the record is already in a terminal state.
        """
        record = self._get(promotion_id)
        if record.is_terminal():
            raise ValueError(
                f"Cannot quarantine promotion '{promotion_id}' — "
                f"already in terminal state '{record.state.value}'"
            )
        record.transition(
            PromotionState.QUARANTINED,
            reason or "Awaiting human review",
        )
        return record

    def accept(
        self, promotion_id: str, reason: str = ""
    ) -> PromotionRecord:
        """Accept a patch, moving it to the ``ACCEPTED`` state.

        Parameters
        ----------
        promotion_id:
            The promotion record identifier.
        reason:
            Explanation for the acceptance decision.

        Returns
        -------
        PromotionRecord
            The updated promotion record.

        Raises
        ------
        KeyError
            If *promotion_id* is not found.
        """
        record = self._get(promotion_id)
        record.transition(
            PromotionState.ACCEPTED,
            reason or "All gates passed",
        )
        return record

    def reject(
        self, promotion_id: str, reason: str = ""
    ) -> PromotionRecord:
        """Reject a patch, moving it to the ``REJECTED`` state.

        Parameters
        ----------
        promotion_id:
            The promotion record identifier.
        reason:
            Explanation for the rejection.

        Returns
        -------
        PromotionRecord
            The updated promotion record.

        Raises
        ------
        KeyError
            If *promotion_id* is not found.
        """
        record = self._get(promotion_id)
        record.transition(
            PromotionState.REJECTED,
            reason or "Failed gates or human decision",
        )
        return record

    def revert(
        self, promotion_id: str, reason: str = ""
    ) -> PromotionRecord:
        """Revert an already-accepted patch to ``REVERTED``.

        Parameters
        ----------
        promotion_id:
            The promotion record identifier.
        reason:
            Explanation for the reversion.

        Returns
        -------
        PromotionRecord
            The updated promotion record.

        Raises
        ------
        KeyError
            If *promotion_id* is not found.
        ValueError
            If the record is not in ``ACCEPTED`` state.
        """
        record = self._get(promotion_id)
        if record.state != PromotionState.ACCEPTED:
            raise ValueError(
                f"Cannot revert promotion '{promotion_id}' — "
                f"not in ACCEPTED state (current: '{record.state.value}')"
            )
        record.transition(
            PromotionState.REVERTED,
            reason or "Post-accept regression detected",
        )
        return record

    def human_review(
        self,
        promotion_id: str,
        decision: str,
        decider: str,
        reason: str = "",
    ) -> PromotionRecord:
        """Record a human review decision and apply the outcome.

        * ``"approve"`` -> transitions to ``ACCEPTED``.
        * ``"reject"`` -> transitions to ``REJECTED``.

        Parameters
        ----------
        promotion_id:
            The promotion record identifier.
        decision:
            ``"approve"`` or ``"reject"``.
        decider:
            Identifier of the human making the decision.
        reason:
            Explanation for the decision.

        Returns
        -------
        PromotionRecord
            The updated promotion record.

        Raises
        ------
        KeyError
            If *promotion_id* is not found.
        ValueError
            If *decision* is not ``"approve"`` or ``"reject"``.
        """
        decision = str(decision).lower().strip()
        if decision not in ("approve", "reject"):
            raise ValueError(
                f"Decision must be 'approve' or 'reject', got {decision!r}"
            )

        record = self._get(promotion_id)
        record.human_decision = decision
        record.human_decider = str(decider) if decider else "unknown"

        if decision == "approve":
            return self.accept(
                promotion_id, f"Human approved by {decider}: {reason}"
            )
        return self.reject(
            promotion_id, f"Human rejected by {decider}: {reason}"
        )

    # -- Queries -------------------------------------------------------

    def get(self, promotion_id: str) -> Optional[PromotionRecord]:
        """Retrieve a promotion record by ID.

        Returns ``None`` if not found (does **not** raise).
        """
        return self._records.get(promotion_id)

    def list_all(self) -> List[PromotionRecord]:
        """Return all promotion records."""
        return list(self._records.values())

    def list_by_state(self, state: PromotionState) -> List[PromotionRecord]:
        """Return all records in the given state.

        Parameters
        ----------
        state:
            The :class:`PromotionState` to filter by.

        Returns
        -------
        list[PromotionRecord]
            Records matching the state.
        """
        if isinstance(state, str):
            state = PromotionState(state.lower())
        return [r for r in self._records.values() if r.state == state]

    def list_by_patch(self, patch_id: str) -> List[PromotionRecord]:
        """Return all promotion records for a given patch ID.

        Returns a list because a patch may have been promoted multiple times.
        """
        return [r for r in self._records.values() if r.patch_id == patch_id]

    def count_by_state(self, state: Optional[PromotionState] = None) -> int:
        """Count records, optionally filtered by state.

        If *state* is ``None``, returns the total count.
        """
        if state is None:
            return len(self._records)
        if isinstance(state, str):
            state = PromotionState(state.lower())
        return sum(1 for r in self._records.values() if r.state == state)

    def delete(self, promotion_id: str) -> bool:
        """Remove a promotion record.

        Returns
        -------
        bool
            ``True`` if a record was removed.
        """
        if promotion_id in self._records:
            del self._records[promotion_id]
            return True
        return False

    def clear(self) -> None:
        """Remove all promotion records."""
        self._records.clear()

    # -- Export --------------------------------------------------------

    def export_all(self) -> List[Dict[str, Any]]:
        """Export all records as serialisable dictionaries."""
        return [r.to_dict() for r in self._records.values()]

    def export_by_state(self, state: PromotionState) -> List[Dict[str, Any]]:
        """Export records in a specific state as serialisable dictionaries."""
        return [r.to_dict() for r in self.list_by_state(state)]

    # -- Internal ------------------------------------------------------

    def _get(self, promotion_id: str) -> PromotionRecord:
        """Internal helper that raises ``KeyError`` on missing IDs."""
        if promotion_id not in self._records:
            raise KeyError(f"Promotion '{promotion_id}' not found")
        return self._records[promotion_id]

    def __len__(self) -> int:
        return len(self._records)

    def __contains__(self, promotion_id: str) -> bool:
        return promotion_id in self._records

    def __repr__(self) -> str:
        counts: Dict[str, int] = {}
        for s in PromotionState:
            counts[s.value] = len(self.list_by_state(s))
        return f"PromotionPipeline(records={len(self._records)}, states={counts})"
