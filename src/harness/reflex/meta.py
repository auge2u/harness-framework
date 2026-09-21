"""Meta-refinement for the reflex (System 1) layer.

The :class:`MetaRefinementAnalyzer` closes the reflex learning loop:

1. **Audit** — ingest :class:`DecisionRecord` values (reflex decisions plus
   optional System 2 outcome labels) and compute per-rule
   :class:`RubricMetrics` (precision / recall / false-positive rate) on a
   deterministic train split.
2. **Propose** — derive rubric refinements (downweighting false-positive
   driving keyword signals, promoting discriminating tokens to
   ``required_signals``) and wrap them in governed
   :class:`~harness.patch.HarnessPatch` objects.
3. **Evaluate** — re-score the held-out split with the proposed rubric via
   a :class:`~harness.reflex.backend.MockReflexBackend` and recommend
   promotion only when precision improves without a recall regression.

Everything is deterministic: no LLM calls, no randomness, no network.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from harness.patch import HarnessPatch, PatchOperation
from harness.reflex.backend import MockReflexBackend, _tokenize
from harness.reflex.rubrics import Rubric, RubricRegistry

__all__ = [
    "DecisionRecord",
    "RubricMetrics",
    "MetaRefinementAnalyzer",
    "rubric_to_dict",
    "rubric_from_dict",
]

#: Fraction of false positives that must contain a signal (while fewer than
#: 30% of true positives do) for the signal to be downweighted.
FP_DOMINANCE_THRESHOLD = 0.7
#: Fraction of true positives below which a signal is considered FP-driving.
TP_RARITY_THRESHOLD = 0.3
#: Minimum token length for discriminating ``required_signals`` candidates.
MIN_REQUIRED_TOKEN_LEN = 4
#: Maximum number of discriminating tokens promoted to ``required_signals``.
MAX_PROMOTED_REQUIRED_SIGNALS = 3
#: Slope below which a precision trend is considered drifting.
DRIFT_SLOPE_THRESHOLD = -0.001
#: Maximum tolerated recall regression when promoting a patch.
MAX_RECALL_REGRESSION = 0.05


@dataclass
class DecisionRecord:
    """One reflex decision plus an optional System 2 outcome label.

    Attributes:
        decision_id: Unique identifier for this decision (drives the
            deterministic train/held-out split).
        rule: Name of the rubric/rule that produced the decision.
        value: Probability or score returned by the reflex primitive.
        confidence: Confidence attached to the decision.
        escalate: Whether the reflex layer flagged/escalated the input.
        snippet: The scored input text (used for re-scoring and signal
            analysis).
        outcome: ``True`` when System 2 confirmed the finding (TP),
            ``False`` when it overturned it (FP), ``None`` when unlabeled.
        timestamp: When the decision was recorded.
    """

    decision_id: str
    rule: str
    value: float
    confidence: float
    escalate: bool
    snippet: str = ""
    outcome: Optional[bool] = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class RubricMetrics:
    """Aggregated precision/recall statistics for one rule.

    ``precision`` and ``recall`` are ``0.0`` when undefined (empty
    denominator).  ``fp_rate`` is ``FP / (FP + TN)`` with the same rule.
    """

    rule: str
    labeled: int
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    precision: float
    recall: float
    fp_rate: float


def rubric_to_dict(rubric: Rubric) -> Dict[str, Any]:
    """Serialise a :class:`Rubric` to a plain, JSON-compatible dictionary."""
    return {
        "rubric_id": rubric.rubric_id,
        "name": rubric.name,
        "version": rubric.version,
        "criteria": list(rubric.criteria),
        "keyword_signals": dict(rubric.keyword_signals),
        "escalation_threshold": float(rubric.escalation_threshold),
        "pass_threshold": float(rubric.pass_threshold),
        "parent_version": rubric.parent_version,
        "motivation": rubric.motivation,
        "required_signals": list(
            getattr(rubric, "required_signals", []) or []
        ),
    }


def rubric_from_dict(data: Dict[str, Any]) -> Rubric:
    """Rebuild a :class:`Rubric` from a dictionary produced by
    :func:`rubric_to_dict`."""
    return Rubric(
        rubric_id=str(data["rubric_id"]),
        name=str(data["name"]),
        version=str(data.get("version", "1.0.0")),
        criteria=list(data.get("criteria", [])),
        keyword_signals=dict(data.get("keyword_signals", {})),
        escalation_threshold=float(data.get("escalation_threshold", 7.0)),
        pass_threshold=float(data.get("pass_threshold", 2.0)),
        parent_version=str(data.get("parent_version", "")),
        motivation=str(data.get("motivation", "")),
        required_signals=list(data.get("required_signals", [])),
    )


class MetaRefinementAnalyzer:
    """Audits reflex decisions against System 2 outcomes; proposes rubric patches.

    Args:
        registry: The :class:`RubricRegistry` holding the live rubric
            versions under audit.
        snapshot_store: Optional content-addressed store (duck-typed on a
            ``put(artifact, artifact_type, metadata)`` method, e.g.
            :class:`~harness.store.snapshots.ContentAddressedStore`) that
            receives proposed rubric dictionaries under
            ``artifact_type="rubric"``.
        held_out_ratio: Fraction of ingested records reserved for patch
            evaluation (deterministic hash split on ``decision_id``).
    """

    def __init__(
        self,
        registry: RubricRegistry,
        snapshot_store: Optional[Any] = None,
        held_out_ratio: float = 0.2,
    ) -> None:
        if not isinstance(registry, RubricRegistry):
            raise TypeError(
                f"Expected RubricRegistry, got {type(registry).__name__}"
            )
        self.registry = registry
        self.snapshot_store = snapshot_store
        self.held_out_ratio = float(held_out_ratio)
        self._records: List[DecisionRecord] = []
        self._backend = MockReflexBackend()

    # -- Ingestion & splitting ------------------------------------------------

    def ingest(self, records: List[DecisionRecord]) -> None:
        """Store *records* and assign each to the train or held-out split.

        The split is deterministic: ``sha256(decision_id)`` decides the
        bucket, so the same record lands in the same split for every
        analyzer instance with the same ``held_out_ratio``.
        """
        for record in records:
            if not isinstance(record, DecisionRecord):
                raise TypeError(
                    f"Expected DecisionRecord, got {type(record).__name__}"
                )
            self._records.append(record)

    def _is_held_out(self, decision_id: str) -> bool:
        """Return ``True`` when *decision_id* hashes into the held-out split."""
        digest = hashlib.sha256(decision_id.encode("utf-8")).hexdigest()
        fraction = int(digest[:12], 16) / float(0xFFFFFFFFFFFF)
        return fraction < self.held_out_ratio

    def train_records(self, rule: Optional[str] = None) -> List[DecisionRecord]:
        """Return ingested records in the train split, optionally filtered by *rule*."""
        return [
            r
            for r in self._records
            if not self._is_held_out(r.decision_id)
            and (rule is None or r.rule == rule)
        ]

    def held_out_records(self, rule: Optional[str] = None) -> List[DecisionRecord]:
        """Return ingested records in the held-out split, optionally filtered by *rule*."""
        return [
            r
            for r in self._records
            if self._is_held_out(r.decision_id)
            and (rule is None or r.rule == rule)
        ]

    # -- Metrics ----------------------------------------------------------------

    @staticmethod
    def _pairs_metrics(
        rule: str, pairs: List[Tuple[bool, bool]]
    ) -> RubricMetrics:
        """Build :class:`RubricMetrics` from ``(predicted_positive, actual)`` pairs."""
        tp = sum(1 for predicted, actual in pairs if predicted and actual)
        fp = sum(1 for predicted, actual in pairs if predicted and not actual)
        fn = sum(1 for predicted, actual in pairs if not predicted and actual)
        tn = sum(1 for predicted, actual in pairs if not predicted and not actual)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        fp_rate = fp / (fp + tn) if (fp + tn) else 0.0
        return RubricMetrics(
            rule=rule,
            labeled=len(pairs),
            true_positives=tp,
            false_positives=fp,
            false_negatives=fn,
            true_negatives=tn,
            precision=round(precision, 4),
            recall=round(recall, 4),
            fp_rate=round(fp_rate, 4),
        )

    @staticmethod
    def _records_metrics(rule: str, records: List[DecisionRecord]) -> RubricMetrics:
        """Build metrics from labeled records using their ``escalate`` flag."""
        pairs = [
            (bool(r.escalate), bool(r.outcome))
            for r in records
            if r.outcome is not None
        ]
        return MetaRefinementAnalyzer._pairs_metrics(rule, pairs)

    def metrics(self, rule: Optional[str] = None) -> Dict[str, RubricMetrics]:
        """Compute per-rule :class:`RubricMetrics` on the train split.

        Args:
            rule: Optional rule name restricting the computation.

        Returns:
            Mapping of rule name to its metrics.  Unlabeled records are
            ignored; rules with no labeled records report zeros.
        """
        buckets: Dict[str, List[DecisionRecord]] = {}
        for record in self.train_records(rule=rule):
            buckets.setdefault(record.rule, []).append(record)
        return {
            name: self._records_metrics(name, records)
            for name, records in buckets.items()
        }

    # -- Drift detection ---------------------------------------------------------

    def detect_drift(self, rule: str, window: int = 50) -> Dict[str, Any]:
        """Detect a declining precision trend for *rule*.

        Labeled records (in ingestion order) are analysed with a sliding
        window of size *window*; the least-squares slope of the per-window
        precision series decides drift.

        Returns:
            ``{"drifting": bool, "slope": float, "windows": [precision, ...]}``.
            Fewer than two full windows yields ``drifting=False``.
        """
        labeled = [
            r
            for r in self._records
            if r.rule == rule and r.outcome is not None
        ]
        window = max(1, int(window))
        windows: List[float] = []
        for start in range(0, len(labeled) - window + 1):
            chunk = labeled[start : start + window]
            metrics = self._records_metrics(rule, chunk)
            windows.append(metrics.precision)
        slope = _least_squares_slope(windows)
        drifting = len(windows) >= 2 and slope < DRIFT_SLOPE_THRESHOLD
        return {
            "drifting": drifting,
            "slope": round(slope, 6),
            "windows": windows,
        }

    # -- Rubric refinement ---------------------------------------------------------

    @staticmethod
    def _bump_minor(version: str) -> str:
        """Increment the minor component of a ``major.minor.patch`` version."""
        parts = str(version).split(".")
        while len(parts) < 3:
            parts.append("0")
        try:
            parts[1] = str(int(parts[1]) + 1)
            parts[2] = "0"
        except ValueError:
            parts.append("1")
        return ".".join(parts)

    def refine_rubric(
        self, rubric: Rubric, labeled: List[DecisionRecord]
    ) -> Rubric:
        """Derive a refined rubric from labeled outcome data (deterministic).

        Strategy:

        * Keyword signals present in more than 70% of overturned decisions
          (``outcome=False``) but fewer than 30% of confirmed ones are
          FP-driving: their weight is halved.
        * Snippet tokens present in at least 70% of confirmed decisions and
          at most 30% of overturned ones are discriminating: up to three
          are promoted into ``required_signals`` so the rubric stops
          applying to inputs that lack them.

        Returns a NEW :class:`Rubric` with the minor version bumped and
        ``parent_version`` pointing at *rubric*'s version.
        """
        fps = [r for r in labeled if r.outcome is False and r.escalate]
        tps = [r for r in labeled if r.outcome is True and r.escalate]

        new_signals: Dict[str, float] = dict(rubric.keyword_signals)
        if fps:
            for signal, delta in rubric.keyword_signals.items():
                if delta <= 0:
                    continue
                needle = signal.lower()
                fp_ratio = (
                    sum(1 for r in fps if needle in r.snippet.lower()) / len(fps)
                )
                tp_ratio = (
                    sum(1 for r in tps if needle in r.snippet.lower()) / len(tps)
                    if tps
                    else 0.0
                )
                if fp_ratio > FP_DOMINANCE_THRESHOLD and tp_ratio < TP_RARITY_THRESHOLD:
                    new_signals[signal] = round(delta / 2.0, 4)

        required = list(getattr(rubric, "required_signals", []) or [])
        if fps and tps:
            fp_token_sets = [_tokenize(r.snippet) for r in fps]
            tp_token_sets = [_tokenize(r.snippet) for r in tps]
            candidates = set().union(*tp_token_sets) if tp_token_sets else set()
            promoted: List[str] = []
            for token in sorted(candidates):
                if len(token) < MIN_REQUIRED_TOKEN_LEN or token in required:
                    continue
                tp_ratio = sum(token in s for s in tp_token_sets) / len(tp_token_sets)
                fp_ratio = sum(token in s for s in fp_token_sets) / len(fp_token_sets)
                if tp_ratio >= FP_DOMINANCE_THRESHOLD and fp_ratio <= TP_RARITY_THRESHOLD:
                    promoted.append(token)
            required.extend(promoted[:MAX_PROMOTED_REQUIRED_SIGNALS])

        new_version = self._bump_minor(rubric.version)
        return Rubric(
            rubric_id=f"{rubric.rubric_id}-v{new_version}",
            name=rubric.name,
            version=new_version,
            criteria=list(rubric.criteria),
            keyword_signals=new_signals,
            escalation_threshold=rubric.escalation_threshold,
            pass_threshold=rubric.pass_threshold,
            parent_version=rubric.version,
            motivation=(
                f"Meta-refinement of {rubric.name} v{rubric.version}: "
                "downweight FP-driving signals / require discriminating signals."
            ),
            required_signals=required,
        )

    # -- Patch proposal & evaluation ------------------------------------------------

    def propose_patch(self, rule: str, motivation: str) -> Optional[HarnessPatch]:
        """Propose a governed :class:`HarnessPatch` refining the rubric *rule*.

        The patch replaces the current rubric dictionary with the
        analyzer-refined one.  Returns ``None`` when there are no labeled
        train records for the rule or the refinement changes nothing.

        When a snapshot store is attached, the proposed rubric dictionary
        is persisted under ``artifact_type="rubric"``.
        """
        try:
            current = self.registry.get(rule)
        except KeyError:
            return None
        labeled = [
            r for r in self.train_records(rule=rule) if r.outcome is not None
        ]
        if not labeled:
            return None
        refined = self.refine_rubric(current, labeled)

        before = rubric_to_dict(current)
        after = rubric_to_dict(refined)
        scored_keys = (
            "criteria",
            "keyword_signals",
            "escalation_threshold",
            "pass_threshold",
            "required_signals",
        )
        if all(before[key] == after[key] for key in scored_keys):
            return None

        if self.snapshot_store is not None:
            self.snapshot_store.put(
                after,
                artifact_type="rubric",
                metadata={"rule": rule, "source": "meta_refinement"},
            )

        return HarnessPatch(
            patch_id=f"meta-{rule}-{uuid.uuid4().hex[:12]}",
            surface="reflex",
            target_id=rule,
            operation=PatchOperation.REPLACE,
            before=before,
            after=after,
            motivation=motivation,
            proposer="meta_refinement_analyzer",
            proposer_type="meta",
        )

    def evaluate_patch(
        self, patch: HarnessPatch, records: List[DecisionRecord]
    ) -> Dict[str, Any]:
        """Re-score held-out records with the proposed rubric and compare metrics.

        Each labeled record's snippet is re-scored via a deterministic
        :class:`MockReflexBackend` with both the ``before`` and ``after``
        rubric dictionaries, and the resulting ``(predicted, actual)``
        pairs are aggregated into :class:`RubricMetrics`.

        Args:
            patch: A patch produced by :meth:`propose_patch`.
            records: Records to evaluate on; when empty, the analyzer's own
                held-out split for ``patch.target_id`` is used.

        Returns:
            ``{"before": RubricMetrics, "after": RubricMetrics,
            "precision_delta": float, "fp_delta": float, "promote": bool}``
            where ``promote`` is ``True`` only when precision improves and
            recall does not regress by more than 5%.
        """
        rule = str(patch.target_id)
        evaluation = list(records) if records else self.held_out_records(rule=rule)
        labeled = [r for r in evaluation if r.outcome is not None]

        before_rubric = rubric_from_dict(patch.before)
        after_rubric = rubric_from_dict(patch.after)

        before = self._rescore_metrics(rule, before_rubric, labeled)
        after = self._rescore_metrics(rule, after_rubric, labeled)

        precision_delta = round(after.precision - before.precision, 4)
        fp_delta = round(after.fp_rate - before.fp_rate, 4)
        promote = (
            after.precision > before.precision
            and after.recall >= before.recall - MAX_RECALL_REGRESSION
        )
        return {
            "before": before,
            "after": after,
            "precision_delta": precision_delta,
            "fp_delta": fp_delta,
            "promote": promote,
        }

    def _rescore_metrics(
        self,
        rule: str,
        rubric: Rubric,
        labeled: List[DecisionRecord],
    ) -> RubricMetrics:
        """Re-score *labeled* records with *rubric* and aggregate metrics."""
        pairs: List[Tuple[bool, bool]] = []
        for record in labeled:
            score = self._backend.score_check(record.snippet, rubric)
            pairs.append((score >= rubric.escalation_threshold, bool(record.outcome)))
        return self._pairs_metrics(rule, pairs)


def _least_squares_slope(values: List[float]) -> float:
    """Return the least-squares slope of *values* over index positions."""
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values))
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return 0.0
    return numerator / denominator
