"""FailureClusterer — group trace records into semantically similar failure
clusters using signature extraction and Jaccard similarity.

The algorithm is intentionally simple (token-level Jaccard) so that it runs
fast on thousands of traces without external dependencies.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from harness.core.types import TraceRecord, FailureSignature, Verdict


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
)


def _normalise_error(text: str) -> str:
    """Normalise an error string for canonical comparison.

    Replaces UUIDs, numbers, and ISO timestamps with placeholders so that
    similar errors produce the same signature.
    """
    text = text.lower().strip()
    text = _UUID_RE.sub("<UUID>", text)
    text = _NUMBER_RE.sub("<NUM>", text)
    text = _TIMESTAMP_RE.sub("<TIME>", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _tokenise_signature(sig: str) -> Set[str]:
    """Split a signature string into tokens by ``|`` and ``=``."""
    parts = re.split(r"[|=]", sig)
    return {p.strip() for p in parts if p.strip()}


# ---------------------------------------------------------------------------
# FailureClusterer
# ---------------------------------------------------------------------------

class FailureClusterer:
    """Group :class:`TraceRecord` instances into failure clusters.

    Parameters
    ----------
    min_cluster_size:
        Minimum number of traces required before a cluster is considered
        *viable* (returned by :meth:`cluster`).
    similarity_threshold:
        Jaccard similarity threshold (0.0–1.0) for assigning a trace to an
        existing cluster versus creating a new one.
    """

    def __init__(
        self,
        min_cluster_size: int = 2,
        similarity_threshold: float = 0.8,
    ) -> None:
        self.min_cluster_size = min_cluster_size
        self.similarity_threshold = similarity_threshold

        # cluster_id -> list of trace_ids
        self._clusters: Dict[str, List[str]] = {}
        # cluster_id -> FailureSignature
        self._signatures: Dict[str, FailureSignature] = {}
        # cluster_id -> canonical signature string (cached for similarity)
        self._canonical: Dict[str, str] = {}

    # -- Signature extraction -------------------------------------------------

    def _extract_signature(self, trace: TraceRecord) -> str:
        """Build a canonical signature string from *trace*.

        Format:
            ``{scenario_id}|{verifier_type}|surfaces={sorted_surfaces}|error={normalized_error}``
        """
        verifier_type = ""
        if trace.verifier_results and isinstance(trace.verifier_results, dict):
            verifier_type = trace.verifier_results.get("verifier_type", "")
            if not verifier_type:
                verifier_type = trace.verifier_results.get("verifier", "")

        surfaces: List[str] = []
        if trace.metadata and isinstance(trace.metadata, dict):
            s = trace.metadata.get("surface")
            if s:
                surfaces.append(str(s))
            s_list = trace.metadata.get("surfaces")
            if isinstance(s_list, (list, tuple)):
                surfaces.extend(str(x) for x in s_list)
        surfaces = sorted(set(surfaces))

        error_text = ""
        if trace.verifier_results and isinstance(trace.verifier_results, dict):
            error_text = trace.verifier_results.get("error", "")
            if not error_text:
                error_text = trace.verifier_results.get("message", "")
        if not error_text and trace.outputs and isinstance(trace.outputs, dict):
            error_text = trace.outputs.get("error", "")
            if not error_text:
                error_text = trace.outputs.get("stderr", "")

        normalised_error = _normalise_error(error_text) if error_text else "none"

        sig = (
            f"{trace.scenario_id}|{verifier_type}"
            f"|surfaces={','.join(surfaces)}"
            f"|error={normalised_error}"
        )
        return sig

    # -- Similarity -----------------------------------------------------------

    def _compute_similarity(self, sig1: str, sig2: str) -> float:
        """Compute token-level Jaccard similarity between two signatures.

        Returns a float in ``[0.0, 1.0]``.
        """
        tokens1 = _tokenise_signature(sig1)
        tokens2 = _tokenise_signature(sig2)
        if not tokens1 and not tokens2:
            return 1.0
        intersection = tokens1 & tokens2
        union = tokens1 | tokens2
        if not union:
            return 0.0
        return len(intersection) / len(union)

    # -- Core clustering ------------------------------------------------------

    def add_trace(self, trace: TraceRecord) -> Optional[str]:
        """Add a single trace to the clusterer.

        1. Extract the canonical signature.
        2. Find the best matching existing cluster (Jaccard >= threshold).
        3. If found, append the trace and update the cluster.
        4. If not, create a new cluster.

        Returns the ``cluster_id`` of the assigned cluster, or ``None`` if
        the trace does not represent a failure.
        """
        if trace.verdict in (Verdict.PASS, Verdict.SKIP):
            return None

        sig = self._extract_signature(trace)

        best_cluster: Optional[str] = None
        best_sim = 0.0
        for cid, csig in self._canonical.items():
            sim = self._compute_similarity(sig, csig)
            if sim >= self.similarity_threshold and sim > best_sim:
                best_sim = sim
                best_cluster = cid

        now = datetime.now(timezone.utc)

        if best_cluster is not None:
            self._clusters[best_cluster].append(trace.trace_id)
            self._canonical[best_cluster] = sig
            existing = self._signatures[best_cluster]
            self._signatures[best_cluster] = FailureSignature(
                cluster_id=existing.cluster_id,
                pattern=sig,
                surfaces=existing.surfaces,
                verifier_type=existing.verifier_type,
                frequency=existing.frequency + 1,
                first_seen=existing.first_seen,
                last_seen=now,
            )
            return best_cluster

        # Create new cluster
        new_cid = f"cluster-{uuid.uuid4().hex[:12]}"
        self._clusters[new_cid] = [trace.trace_id]
        self._canonical[new_cid] = sig

        surfaces: List[str] = []
        if trace.metadata and isinstance(trace.metadata, dict):
            s = trace.metadata.get("surface")
            if s:
                surfaces.append(str(s))
            s_list = trace.metadata.get("surfaces")
            if isinstance(s_list, (list, tuple)):
                surfaces.extend(str(x) for x in s_list)

        verifier_type = ""
        if trace.verifier_results and isinstance(trace.verifier_results, dict):
            verifier_type = trace.verifier_results.get("verifier_type", "")
            if not verifier_type:
                verifier_type = trace.verifier_results.get("verifier", "")

        self._signatures[new_cid] = FailureSignature(
            cluster_id=new_cid,
            pattern=sig,
            surfaces=surfaces,
            verifier_type=verifier_type,
            frequency=1,
            first_seen=now,
            last_seen=now,
        )
        return new_cid

    def cluster(self, traces: List[TraceRecord]) -> Dict[str, List[TraceRecord]]:
        """Cluster *traces* in batch mode.

        Returns a mapping from ``cluster_id`` to the list of
        :class:`TraceRecord` objects in that cluster (only clusters meeting
        ``min_cluster_size`` are included).
        """
        trace_map = {t.trace_id: t for t in traces}

        for trace in traces:
            self.add_trace(trace)

        result: Dict[str, List[TraceRecord]] = {}
        for cid, tid_list in self._clusters.items():
            if len(tid_list) >= self.min_cluster_size:
                result[cid] = [trace_map[tid] for tid in tid_list if tid in trace_map]
        return result

    # -- Queries --------------------------------------------------------------

    def get_signature(self, cluster_id: str) -> FailureSignature:
        """Return the stored :class:`FailureSignature` for *cluster_id*.

        Raises
        ------
        KeyError
            If *cluster_id* is not known.
        """
        if cluster_id not in self._signatures:
            raise KeyError(f"Unknown cluster_id: {cluster_id!r}")
        return self._signatures[cluster_id]

    def get_clusters_for_surface(self, surface: str) -> List[FailureSignature]:
        """Return all failure signatures whose surface list contains *surface*."""
        return [
            sig for sig in self._signatures.values()
            if surface in sig.surfaces
        ]

    def get_relationship_impact(self) -> Dict[str, Dict[str, float]]:
        """Compute co-occurrence rates between every pair of surfaces.

        For surfaces *A* and *B*:

        .. math::

           cooccurrence(A, B) = |clusters with both A and B|
                                / |clusters with A or B|

        Returns a nested dict ``{surface_A: {surface_B: rate}}``.  The
        diagonal is always ``1.0``.
        """
        all_surfaces: Set[str] = set()
        cluster_surfaces: Dict[str, Set[str]] = {}
        for cid, sig in self._signatures.items():
            surf_set = set(sig.surfaces)
            cluster_surfaces[cid] = surf_set
            all_surfaces.update(surf_set)

        result: Dict[str, Dict[str, float]] = {}
        surfaces = sorted(all_surfaces)

        for i, s_a in enumerate(surfaces):
            result[s_a] = {}
            for s_b in surfaces[i:]:
                both = 0
                either = 0
                for cid, surf_set in cluster_surfaces.items():
                    has_a = s_a in surf_set
                    has_b = s_b in surf_set
                    if has_a and has_b:
                        both += 1
                    if has_a or has_b:
                        either += 1
                rate = both / either if either > 0 else 0.0
                result[s_a][s_b] = rate
                if s_a != s_b:
                    result.setdefault(s_b, {})[s_a] = rate

        return result

    def get_all_signatures(self) -> List[FailureSignature]:
        """Return all stored failure signatures."""
        return list(self._signatures.values())

    def get_cluster_count(self) -> int:
        """Return the total number of clusters (including sub-threshold)."""
        return len(self._clusters)
