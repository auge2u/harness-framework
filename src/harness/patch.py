"""HarnessPatch -- the unit of harness edit, lineage, evaluation, and rollback."""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from enum import Enum

from harness.utils.diffing import structural_diff


class PatchOperation(Enum):
    """Supported patch operations."""

    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"


@dataclass
class HarnessPatch:
    """A single, invertible edit to a harness surface.

    Every harness edit produces a :class:`HarnessPatch`. Patches are the
    unit of:

    - **lineage** -- what changed when
    - **evaluation** -- did this change help?
    - **rollback** -- how to undo

    Attributes:
        patch_id: Unique identifier for this patch.
        surface: Which of the declared harness surfaces is modified.
        target_id: Specific fragment, tool, or config path within the surface.
        operation: Type of edit -- ``add``, ``replace``, or ``remove``.
        before: Previous value (for rollback and diff display).
        after: Proposed new value.
        inverse: Patch that restores the previous state.
        motivation: Reference to a :class:`~harness.core.types.FailureSignature`
            or other evidence that motivated this patch.
        proposer: Name of the plugin or agent that proposed this patch.
        proposer_type: ``"self"`` | ``"meta"`` | ``"manual"``.
        created_at: Timestamp when the patch was created.
        scope: Impact scope -- ``"ATOMIC"``, ``"COMPONENT"``, or ``"SYSTEM"``.
        estimated_risk: Risk estimate in the range ``[0, 1]`` provided by
            the proposer.
    """

    patch_id: str
    surface: str
    target_id: str
    operation: PatchOperation
    before: Any
    after: Any
    inverse: Optional["HarnessPatch"] = None
    motivation: str = ""
    proposer: str = ""
    proposer_type: str = "self"  # "self" | "meta" | "manual"
    created_at: datetime = field(default_factory=datetime.now)
    scope: str = "ATOMIC"  # ATOMIC / COMPONENT / SYSTEM
    estimated_risk: float = 0.0

    def __post_init__(self):
        if self.inverse is None and self.operation != PatchOperation.REMOVE:
            # Auto-generate inverse patch for add/replace operations.
            # For ADD, the inverse is REMOVE.
            # For REPLACE, the inverse is REPLACE (swap before/after).
            # We use object.__new__ to bypass the dataclass __init__
            # and avoid infinite recursion in __post_init__.
            inverse_op = (
                PatchOperation.REMOVE
                if self.operation == PatchOperation.ADD
                else PatchOperation.REPLACE
            )
            inv = object.__new__(HarnessPatch)
            inv.patch_id = f"{self.patch_id}-inverse"
            inv.surface = self.surface
            inv.target_id = self.target_id
            inv.operation = inverse_op
            inv.before = self.after
            inv.after = self.before
            inv.inverse = None  # Don't recurse -- the inverse of an inverse is the original
            inv.motivation = f"Rollback of {self.patch_id}"
            inv.proposer = "system"
            inv.proposer_type = "manual"
            inv.created_at = self.created_at
            inv.scope = self.scope
            inv.estimated_risk = self.estimated_risk
            self.inverse = inv

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dictionary.

        Returns:
            A JSON-compatible dict representation of this patch.
        """
        result: Dict[str, Any] = {
            "patch_id": self.patch_id,
            "surface": self.surface,
            "target_id": self.target_id,
            "operation": self.operation.value,
            "before": _serialise_value(self.before),
            "after": _serialise_value(self.after),
            "motivation": self.motivation,
            "proposer": self.proposer,
            "proposer_type": self.proposer_type,
            "created_at": self.created_at.isoformat(),
            "scope": self.scope,
            "estimated_risk": self.estimated_risk,
        }
        if self.inverse is not None:
            result["inverse"] = self.inverse.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HarnessPatch":
        """Deserialize from a plain dictionary.

        Args:
            data: Dictionary previously produced by :meth:`to_dict`.

        Returns:
            A fully reconstructed :class:`HarnessPatch`.
        """
        inverse_data = data.get("inverse")
        inverse = None
        if inverse_data is not None:
            inverse = cls.from_dict(inverse_data)

        # Parse datetime if it's a string; otherwise use as-is.
        created_raw = data.get("created_at", datetime.now())
        if isinstance(created_raw, str):
            created_at = datetime.fromisoformat(created_raw)
        else:
            created_at = created_raw

        # Parse operation enum.
        op_raw = data.get("operation", "replace")
        if isinstance(op_raw, str):
            operation = PatchOperation(op_raw)
        else:
            operation = op_raw

        patch = cls(
            patch_id=data.get("patch_id", ""),
            surface=data.get("surface", ""),
            target_id=data.get("target_id", ""),
            operation=operation,
            before=_deserialise_value(data.get("before")),
            after=_deserialise_value(data.get("after")),
            inverse=inverse,
            motivation=data.get("motivation", ""),
            proposer=data.get("proposer", ""),
            proposer_type=data.get("proposer_type", "self"),
            created_at=created_at,
            scope=data.get("scope", "ATOMIC"),
            estimated_risk=float(data.get("estimated_risk", 0.0)),
        )
        return patch

    # ------------------------------------------------------------------
    # Static factory methods for diffing
    # ------------------------------------------------------------------

    @staticmethod
    def from_proposal(proposal: Any) -> List["HarnessPatch"]:
        """Convert a HarnessProposal into a list of HarnessPatch objects.

        The proposal's ``changes`` dict is diffed against an empty baseline to
        produce patches. Each top-level key in ``changes`` becomes a patch
        targeting that surface.

        Special operation keys within change values:
        - ``{"_operation": "remove"}`` → :py:attr:`PatchOperation.REMOVE`
        - ``{"_operation": "add"}`` → :py:attr:`PatchOperation.ADD`
        - Otherwise → :py:attr:`PatchOperation.REPLACE`

        Args:
            proposal: A :class:`~harness.core.types.HarnessProposal` instance.

        Returns:
            Ordered list of :class:`HarnessPatch` objects derived from the
            proposal's ``changes``.
        """
        patches: List[HarnessPatch] = []
        if proposal is None:
            return patches

        changes = getattr(proposal, "changes", None) or {}
        proposer_type = getattr(proposal, "proposer_type", "self")
        rationale = getattr(proposal, "rationale", "")
        timestamp = datetime.now()

        # Handle flat _generate_change format: {"_operation": "replace",
        # "_surface": "identity", "_value": "v", "_rationale": "..."}
        if (
            isinstance(changes, dict)
            and "_operation" in changes
            and "_surface" in changes
        ):
            op = changes["_operation"]
            surface = changes["_surface"]
            val = changes.get("_value")
            if op == "remove":
                operation = PatchOperation.REMOVE
                before = val
                after = None
            elif op == "add":
                operation = PatchOperation.ADD
                before = None
                after = val
            else:
                operation = PatchOperation.REPLACE
                before = None
                after = val
            patches.append(
                HarnessPatch(
                    patch_id=_gen_patch_id(),
                    surface=str(surface),
                    target_id=str(surface),
                    operation=operation,
                    before=before,
                    after=after,
                    motivation=changes.get("_rationale", rationale),
                    proposer=proposer_type,
                    proposer_type=proposer_type,
                    created_at=timestamp,
                )
            )
            return patches

        # Handle _scopes format: {"_scopes": [{"scope": "add_scenario",
        # "name": "coverage_identity"}, ...]}
        if isinstance(changes, dict) and "_scopes" in changes:
            for scope_item in changes["_scopes"]:
                if isinstance(scope_item, dict):
                    scope_name = scope_item.get("scope", "unknown")
                    target = scope_item.get("name", scope_name)
                    patches.append(
                        HarnessPatch(
                            patch_id=_gen_patch_id(),
                            surface=str(target),
                            target_id=str(target),
                            operation=PatchOperation.REPLACE,
                            before=None,
                            after=scope_item,
                            motivation=rationale,
                            proposer=proposer_type,
                            proposer_type=proposer_type,
                            created_at=timestamp,
                        )
                    )
            return patches

        for key, value in changes.items():
            # Skip internal metadata keys
            if key.startswith("_"):
                continue

            if isinstance(value, dict) and value.get("_operation") == "remove":
                operation = PatchOperation.REMOVE
                before = value.get("_value", value)
                after = None
            elif isinstance(value, dict) and value.get("_operation") == "add":
                operation = PatchOperation.ADD
                before = None
                after = value.get("_value", value)
            else:
                operation = PatchOperation.REPLACE
                before = None
                after = value

            patch = HarnessPatch(
                patch_id=_gen_patch_id(),
                surface=str(key),
                target_id=str(key),
                operation=operation,
                before=before,
                after=after,
                motivation=rationale,
                proposer=proposer_type,
                proposer_type=proposer_type,
                created_at=timestamp,
            )
            patches.append(patch)

        return patches

    @staticmethod
    def diff_surfaces(
        old_surfaces: List[Dict[str, Any]], new_surfaces: List[Dict[str, Any]]
    ) -> List["HarnessPatch"]:
        """Generate patches from a surface list diff.

        Compares two lists of surface dictionaries and produces
        :class:`HarnessPatch` objects for every addition, removal, and
        field-level change.

        Args:
            old_surfaces: Baseline list of surface dicts.
            new_surfaces: Modified list of surface dicts.

        Returns:
            Ordered list of patches that transform *old_surfaces* into
            *new_surfaces*.
        """
        patches: List[HarnessPatch] = []
        timestamp = datetime.now()

        # Index by name for O(1) lookups.
        old_by_name: Dict[str, Dict[str, Any]] = {}
        for s in old_surfaces:
            if isinstance(s, dict):
                name = s.get("name", "")
                if name:
                    old_by_name[name] = s

        new_by_name: Dict[str, Dict[str, Any]] = {}
        for s in new_surfaces:
            if isinstance(s, dict):
                name = s.get("name", "")
                if name:
                    new_by_name[name] = s

        old_names = set(old_by_name.keys())
        new_names = set(new_by_name.keys())

        # --- REMOVED surfaces --------------------------------------------
        for name in sorted(old_names - new_names):
            patches.append(
                HarnessPatch(
                    patch_id=_gen_patch_id(),
                    surface="surfaces",
                    target_id=name,
                    operation=PatchOperation.REMOVE,
                    before=old_by_name[name],
                    after=None,
                    created_at=timestamp,
                )
            )

        # --- ADDED surfaces ----------------------------------------------
        for name in sorted(new_names - old_names):
            patches.append(
                HarnessPatch(
                    patch_id=_gen_patch_id(),
                    surface="surfaces",
                    target_id=name,
                    operation=PatchOperation.ADD,
                    before=None,
                    after=new_by_name[name],
                    created_at=timestamp,
                )
            )

        # --- MODIFIED surfaces (field-level diffs) -----------------------
        for name in sorted(old_names & new_names):
            old_surf = old_by_name[name]
            new_surf = new_by_name[name]
            diff_result = structural_diff(old_surf, new_surf)

            # Added fields.
            for field_name, value in diff_result.get("added", {}).items():
                patches.append(
                    HarnessPatch(
                        patch_id=_gen_patch_id(),
                        surface="surfaces",
                        target_id=f"{name}.{field_name}",
                        operation=PatchOperation.ADD,
                        before=None,
                        after=value,
                        created_at=timestamp,
                    )
                )

            # Removed fields.
            for field_name, value in diff_result.get("removed", {}).items():
                patches.append(
                    HarnessPatch(
                        patch_id=_gen_patch_id(),
                        surface="surfaces",
                        target_id=f"{name}.{field_name}",
                        operation=PatchOperation.REMOVE,
                        before=value,
                        after=None,
                        created_at=timestamp,
                    )
                )

            # Changed fields.
            for field_name, change_info in diff_result.get("changed", {}).items():
                old_val = change_info.get("old")
                new_val = change_info.get("new")
                patches.append(
                    HarnessPatch(
                        patch_id=_gen_patch_id(),
                        surface="surfaces",
                        target_id=f"{name}.{field_name}",
                        operation=PatchOperation.REPLACE,
                        before=old_val,
                        after=new_val,
                        created_at=timestamp,
                    )
                )

        return patches

    @staticmethod
    def diff_config(
        old_config: Dict[str, Any], new_config: Dict[str, Any]
    ) -> List["HarnessPatch"]:
        """Generate patches from a config dict diff.

        Uses :func:`~harness.utils.diffing.structural_diff` to compare the
        two configs and converts every difference into a
        :class:`HarnessPatch`.

        Args:
            old_config: Baseline configuration dictionary.
            new_config: Modified configuration dictionary.

        Returns:
            Ordered list of patches that transform *old_config* into
            *new_config*.
        """
        patches: List[HarnessPatch] = []
        timestamp = datetime.now()

        diff_result = structural_diff(old_config, new_config)

        # Added top-level keys.
        for key, value in diff_result.get("added", {}).items():
            patches.append(
                HarnessPatch(
                    patch_id=_gen_patch_id(),
                    surface="config",
                    target_id=key,
                    operation=PatchOperation.ADD,
                    before=None,
                    after=copy.deepcopy(value),
                    created_at=timestamp,
                )
            )

        # Removed top-level keys.
        for key, value in diff_result.get("removed", {}).items():
            patches.append(
                HarnessPatch(
                    patch_id=_gen_patch_id(),
                    surface="config",
                    target_id=key,
                    operation=PatchOperation.REMOVE,
                    before=copy.deepcopy(value),
                    after=None,
                    created_at=timestamp,
                )
            )

        # Changed top-level keys -- recurse for nested dicts, emit flat
        # replace for primitives.
        for key, change_info in diff_result.get("changed", {}).items():
            old_val = old_config.get(key)
            new_val = new_config.get(key)

            if isinstance(old_val, dict) and isinstance(new_val, dict):
                # Recurse into nested dict changes.
                nested_patches = HarnessPatch.diff_config(old_val, new_val)
                for np in nested_patches:
                    # Re-parent the patch to reflect the full config path.
                    np.patch_id = _gen_patch_id()
                    np.surface = "config"
                    np.target_id = f"{key}.{np.target_id}"
                    np.created_at = timestamp
                    patches.append(np)
            else:
                # Flat replace for primitive or list changes.
                patches.append(
                    HarnessPatch(
                        patch_id=_gen_patch_id(),
                        surface="config",
                        target_id=key,
                        operation=PatchOperation.REPLACE,
                        before=copy.deepcopy(old_val),
                        after=copy.deepcopy(new_val),
                        created_at=timestamp,
                    )
                )

        return patches

    def __repr__(self) -> str:
        return (
            f"HarnessPatch({self.patch_id!r}, {self.surface!r}, "
            f"{self.target_id!r}, {self.operation.value!r})"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gen_patch_id() -> str:
    """Generate a unique patch identifier."""
    return f"patch-{uuid.uuid4().hex[:12]}"


def _serialise_value(value: Any) -> Any:
    """Make a value JSON-serialisable.

    Handles enums, dataclasses, and common container types.
    """
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _serialise_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialise_value(v) for v in value]
    if isinstance(value, tuple):
        return [_serialise_value(v) for v in value]
    return value


def _deserialise_value(value: Any) -> Any:
    """Restore a value from its serialised form.

    Currently a pass-through -- consumers can apply further
    deserialisation if they store non-primitive types.
    """
    return value
