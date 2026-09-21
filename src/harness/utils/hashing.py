"""Deterministic hashing utilities for Harness configurations and dicts."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def hash_dict(data: dict) -> str:
    """Return a deterministic SHA-256 hex digest of *data*.

    The dictionary is serialised to JSON with keys sorted recursively so
    that logically equivalent dictionaries produce the same hash regardless
    of insertion order.
    """
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=True, default=_json_default)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_config(config: "HarnessConfig") -> str:
    """Hash a :class:`HarnessConfig` instance via its dictionary representation."""
    return hash_dict(config.to_dict())


def _json_default(obj: Any) -> Any:
    """Fallback encoder for objects that the standard JSON encoder cannot handle."""
    if hasattr(obj, "name") and hasattr(obj, "value"):
        # Handles Enum instances
        return obj.name
    if hasattr(obj, "isoformat"):
        # Handles datetime objects
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
