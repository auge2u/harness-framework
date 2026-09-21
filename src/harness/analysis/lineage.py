"""HarnessLineage — versioned configuration history with branching and merge.

The lineage store keeps an append-only ``nodes.jsonl`` file where each line
is a JSON-serialised :class:`LineageNode`.  A separate ``branches.json``
file tracks named branch pointers (branch name -> version).
"""

from __future__ import annotations

import copy
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from harness.core.config import HarnessConfig
from harness.core.types import Surface
from harness.store.snapshots import ContentAddressedStore, SnapshotRef
from harness.utils.hashing import hash_dict
from harness.utils.diffing import structural_diff


# ---------------------------------------------------------------------------
# LineageNode
# ---------------------------------------------------------------------------

@dataclass
class LineageNode:
    """A single checkpoint in the Harness configuration lineage.

    Attributes
    ----------
    version:
        Unique version identifier — ``f"{hash[:8]}-{timestamp}"``.
    parent:
        Version identifier of the parent node, or ``None`` for the root.
    config_hash:
        SHA-256 hex digest of the serialised config snapshot.
    config_snapshot:
        The full configuration as a plain dictionary.
    proposal_id:
        If this node was created by applying a proposal, the proposal's UUID;
        otherwise ``None``.
    created_at:
        ISO-8601 timestamp of when the node was committed.
    metrics:
        Optional runtime metrics captured at commit time.
    snapshot_hash:
        Content hash of the config snapshot in the attached
        :class:`ContentAddressedStore`, or ``""`` when no store is
        configured.
    """

    version: str
    parent: Optional[str] = None
    config_hash: str = ""
    config_snapshot: Dict[str, Any] = field(default_factory=dict)
    proposal_id: Optional[str] = None
    created_at: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    snapshot_hash: str = ""


# ---------------------------------------------------------------------------
# HarnessLineage
# ---------------------------------------------------------------------------

class HarnessLineage:
    """Versioned configuration store with branching and three-way merge.

    Parameters
    ----------
    store_path:
        Directory path where ``nodes.jsonl`` and ``branches.json`` live.
        Created automatically if it does not exist.
    snapshot_store:
        Optional :class:`ContentAddressedStore`.  When provided, every
        :meth:`commit` also stores the config snapshot content-addressed
        (deduplicated) and records the returned content hash on the node.
        When ``None`` behaviour is identical to a store-less lineage.
    """

    _NODES_FILE = "nodes.jsonl"
    _BRANCHES_FILE = "branches.json"

    def __init__(
        self,
        store_path: str = ".harness_lineage",
        snapshot_store: Optional[ContentAddressedStore] = None,
    ) -> None:
        self._root = Path(store_path)
        self._root.mkdir(parents=True, exist_ok=True)
        self._snapshot_store = snapshot_store

        self._nodes_path = self._root / self._NODES_FILE
        self._branches_path = self._root / self._BRANCHES_FILE

        self._nodes_path.touch(exist_ok=True)
        if not self._branches_path.exists():
            self._branches_path.write_text("{}", encoding="utf-8")

    # -- Internal helpers -----------------------------------------------------

    def _append_node(self, node: LineageNode) -> None:
        """Append a single node to ``nodes.jsonl``."""
        with self._nodes_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(node), default=str) + "\n")

    def _read_nodes(self) -> List[LineageNode]:
        """Read all nodes from ``nodes.jsonl``."""
        nodes: List[LineageNode] = []
        if not self._nodes_path.exists():
            return nodes
        with self._nodes_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    nodes.append(LineageNode(**data))
                except (json.JSONDecodeError, TypeError):
                    continue
        return nodes

    def _read_branches(self) -> Dict[str, str]:
        """Read the branches file."""
        try:
            return json.loads(self._branches_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _write_branches(self, branches: Dict[str, str]) -> None:
        """Write the branches file atomically."""
        tmp = self._branches_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(branches, indent=2), encoding="utf-8")
        tmp.replace(self._branches_path)

    def _latest_version(self) -> Optional[str]:
        """Return the version of the most recently committed node."""
        nodes = self._read_nodes()
        return nodes[-1].version if nodes else None

    # -- Public API -----------------------------------------------------------

    def commit(
        self,
        config: HarnessConfig,
        proposal_id: Optional[str] = None,
    ) -> LineageNode:
        """Commit *config* as a new node in the lineage.

        Parameters
        ----------
        config:
            The configuration to checkpoint.
        proposal_id:
            Optional UUID of the proposal that produced this config.

        Returns
        -------
        LineageNode
            The newly created node.
        """
        snapshot = config.to_dict()
        config_hash = hash_dict(snapshot)
        timestamp = time.time_ns() // 1_000_000  # millisecond precision
        version = f"{config_hash[:8]}-{timestamp}"
        parent = self._latest_version()

        snapshot_hash = ""
        if self._snapshot_store is not None:
            ref = self._snapshot_store.put(
                snapshot,
                artifact_type="config",
                metadata={"version": version, "proposal_id": proposal_id},
            )
            snapshot_hash = ref.content_hash

        node = LineageNode(
            version=version,
            parent=parent,
            config_hash=config_hash,
            config_snapshot=snapshot,
            proposal_id=proposal_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            metrics={},
            snapshot_hash=snapshot_hash,
        )
        self._append_node(node)
        return node

    def get_node(self, version: str) -> Optional[LineageNode]:
        """Look up a node by *version*."""
        nodes = self._read_nodes()
        for node in reversed(nodes):
            if node.version == version:
                return node
        return None

    def get_history(self, version: Optional[str] = None) -> List[LineageNode]:
        """Follow parent pointers backward from *version* (or the latest).

        Returns the lineage as a list ordered **oldest first**.
        """
        nodes = self._read_nodes()
        node_map = {n.version: n for n in nodes}

        target = version or self._latest_version()
        if target is None:
            return []

        history: List[LineageNode] = []
        current = target
        seen: set = set()
        while current and current not in seen:
            seen.add(current)
            node = node_map.get(current)
            if node is None:
                break
            history.append(node)
            current = node.parent

        history.reverse()
        return history

    def diff(self, v1: str, v2: str) -> Dict[str, Any]:
        """Compute the structural diff between two lineage nodes."""
        n1 = self.get_node(v1)
        n2 = self.get_node(v2)
        if n1 is None or n2 is None:
            missing = []
            if n1 is None:
                missing.append(v1)
            if n2 is None:
                missing.append(v2)
            raise ValueError(f"Version(s) not found in lineage: {missing}")

        return structural_diff(n1.config_snapshot, n2.config_snapshot)

    def merge(
        self,
        base: str,
        branch1: str,
        branch2: str,
        strategy: str = "union",
    ) -> HarnessConfig:
        """Three-way merge of two branch tips against a common base.

        Parameters
        ----------
        base, branch1, branch2:
            Version strings for the common ancestor and the two branch tips.
        strategy:
            Currently only ``"union"`` is supported.

            *union* combines:
            - All surfaces (by name, merging thresholds with ``max``).
            - All verifiers (union by type name).
            - Lower ``held_out_ratio`` (more conservative).

        Returns
        -------
        HarnessConfig
            A new configuration representing the merged result.

        Raises
        ------
        ValueError
            If any version is not found, or *strategy* is unsupported.
        """
        if strategy not in ("union",):
            raise ValueError(f"Unsupported merge strategy: {strategy!r}")

        base_node = self.get_node(base)
        b1_node = self.get_node(branch1)
        b2_node = self.get_node(branch2)

        if base_node is None:
            raise ValueError(f"Base version not found: {base!r}")
        if b1_node is None:
            raise ValueError(f"Branch1 version not found: {branch1!r}")
        if b2_node is None:
            raise ValueError(f"Branch2 version not found: {branch2!r}")

        c_base = HarnessConfig.from_dict(base_node.config_snapshot)
        c1 = HarnessConfig.from_dict(b1_node.config_snapshot)
        c2 = HarnessConfig.from_dict(b2_node.config_snapshot)

        if strategy == "union":
            return self._merge_union(c_base, c1, c2)

        raise RuntimeError("Unexpected merge strategy")  # pragma: no cover

    def _merge_union(
        self,
        base: HarnessConfig,
        c1: HarnessConfig,
        c2: HarnessConfig,
    ) -> HarnessConfig:
        """Union merge: combine surfaces, verifiers, prefer conservative values."""
        # Surfaces: merge by name, preferring higher risk_score
        merged_surfaces: Dict[str, Surface] = {}
        for surf in base.surfaces:
            merged_surfaces[surf.name] = copy.deepcopy(surf)

        for cfg in (c1, c2):
            for surf in cfg.surfaces:
                if surf.name in merged_surfaces:
                    existing = merged_surfaces[surf.name]
                    # Prefer higher risk_score
                    existing.risk_score = max(existing.risk_score, surf.risk_score)
                    # Merge thresholds if present in schema
                    if "threshold" in surf.schema:
                        existing.schema.setdefault("threshold", 0.0)
                        existing.schema["threshold"] = max(
                            existing.schema.get("threshold", 0.0),
                            surf.schema.get("threshold", 0.0),
                        )
                    # Merge other schema keys
                    for k, v in surf.schema.items():
                        if k not in existing.schema:
                            existing.schema[k] = v
                    # Merge dependencies
                    existing_deps = set(existing.dependencies)
                    existing_deps.update(surf.dependencies)
                    existing.dependencies = sorted(existing_deps)
                else:
                    merged_surfaces[surf.name] = copy.deepcopy(surf)

        # Verifiers: union by type name
        merged_verifiers: List[Dict[str, Any]] = []
        seen_vtypes: set = set()
        for v in base.verifiers + c1.verifiers + c2.verifiers:
            vtype = v.get("type", "") if isinstance(v, dict) else str(v)
            if vtype not in seen_vtypes:
                seen_vtypes.add(vtype)
                merged_verifiers.append(copy.deepcopy(v))

        # Held-out ratio: prefer lower (more conservative)
        held_out_ratio = min(base.held_out_ratio, c1.held_out_ratio, c2.held_out_ratio)

        # Scenarios: union of keys
        merged_scenarios = dict(base.scenarios)
        for cfg in (c1, c2):
            for key, val in cfg.scenarios.items():
                if key not in merged_scenarios:
                    merged_scenarios[key] = copy.deepcopy(val)

        # Build merged snapshot
        merged_snapshot = base.to_dict()
        merged_snapshot["surfaces"] = [
            HarnessConfig._surface_to_dict(s) for s in merged_surfaces.values()
        ]
        merged_snapshot["verifiers"] = merged_verifiers
        merged_snapshot["held_out_ratio"] = held_out_ratio
        merged_snapshot["scenarios"] = merged_scenarios

        return HarnessConfig.from_dict(merged_snapshot)

    def branch(self, from_version: str, branch_name: str) -> str:
        """Create (or update) a named branch pointing at *from_version*.

        Returns the *from_version* that the branch now points to.
        """
        branches = self._read_branches()
        branches[branch_name] = from_version
        self._write_branches(branches)
        return from_version

    def checkout(self, version: str) -> HarnessConfig:
        """Reconstruct a :class:`HarnessConfig` from a lineage node.

        Raises
        ------
        ValueError
            If *version* is not found.
        """
        node = self.get_node(version)
        if node is None:
            raise ValueError(f"Version not found in lineage: {version!r}")
        return HarnessConfig.from_dict(node.config_snapshot)

    def checkout_by_hash(self, content_hash: str) -> Dict[str, Any]:
        """Load a config snapshot from the content-addressed store by hash.

        Returns the reconstructed configuration as a plain dictionary
        (the same shape as :meth:`HarnessConfig.to_dict`).

        Raises
        ------
        ValueError
            If no snapshot store is configured.
        harness.store.snapshots.SnapshotNotFoundError
            If *content_hash* is not present in the store.
        """
        if self._snapshot_store is None:
            raise ValueError("No snapshot store configured for this lineage")
        return self._snapshot_store.get_typed(content_hash, "config")

    def snapshot_rubric(
        self,
        rubric_dict: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SnapshotRef:
        """Store a reflex rubric artifact content-addressed.

        Parameters
        ----------
        rubric_dict:
            The rubric payload (e.g. ``dataclasses.asdict(rubric)``).
        metadata:
            Optional free-form metadata recorded in the store index.

        Returns
        -------
        SnapshotRef
            Pointer to the stored rubric snapshot (``artifact_type="rubric"``).

        Raises
        ------
        ValueError
            If no snapshot store is configured.
        """
        if self._snapshot_store is None:
            raise ValueError("No snapshot store configured for this lineage")
        return self._snapshot_store.put(
            rubric_dict, artifact_type="rubric", metadata=metadata
        )

    def list_branches(self) -> Dict[str, str]:
        """Return a mapping of branch name -> version string."""
        return self._read_branches()

    def get_latest(self) -> Optional[LineageNode]:
        """Return the most recently committed node, or ``None``."""
        nodes = self._read_nodes()
        return nodes[-1] if nodes else None

    def gc(self, keep_last: int = 100) -> int:
        """Compact ``nodes.jsonl`` by rewriting only the last *keep_last* nodes.

        Returns the number of nodes removed.
        """
        nodes = self._read_nodes()
        if len(nodes) <= keep_last:
            return 0

        kept = nodes[-keep_last:]
        tmp = self._nodes_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for node in kept:
                fh.write(json.dumps(asdict(node), default=str) + "\n")
        tmp.replace(self._nodes_path)
        return len(nodes) - len(kept)
