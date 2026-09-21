"""Structural diffing for plain Python dictionaries."""

from typing import Any, Dict, List, Set


def structural_diff(old: dict, new: dict) -> dict:
    """Compute a recursive structural diff between two dictionaries.

    Returns a dictionary with three keys:

    * ``added``    — keys present in *new* but not in *old*.
    * ``removed``  — keys present in *old* but not in *new*.
    * ``changed``  — keys present in both but with differing values.

    For nested dictionaries the diff is computed recursively.  For list
    values the comparison is performed as a set-difference (order is
    ignored).  Primitive values are recorded directly in ``changed``.

    Args:
        old: The baseline dictionary.
        new: The updated dictionary.

    Returns:
        A dict of the shape ``{"added": {}, "removed": {}, "changed": {}}``.
    """
    result: Dict[str, Any] = {"added": {}, "removed": {}, "changed": {}}
    old_keys = set(old.keys())
    new_keys = set(new.keys())

    # --- added keys ----------------------------------------------------
    for key in new_keys - old_keys:
        result["added"][key] = new[key]

    # --- removed keys --------------------------------------------------
    for key in old_keys - new_keys:
        result["removed"][key] = old[key]

    # --- common keys ---------------------------------------------------
    for key in old_keys & new_keys:
        old_val = old[key]
        new_val = new[key]

        if old_val == new_val:
            continue

        if isinstance(old_val, dict) and isinstance(new_val, dict):
            nested = structural_diff(old_val, new_val)
            if any(nested[k] for k in ("added", "removed", "changed")):
                result["changed"][key] = nested
        elif isinstance(old_val, list) and isinstance(new_val, list):
            old_set = _list_to_set(old_val)
            new_set = _list_to_set(new_val)
            if old_set != new_set:
                result["changed"][key] = {
                    "added": list(new_set - old_set),
                    "removed": list(old_set - new_set),
                }
        else:
            result["changed"][key] = {"old": old_val, "new": new_val}

    return result


def _list_to_set(lst: List[Any]) -> Set[Any]:
    """Convert a list to a set of hashable items for comparison."""
    result: Set[Any] = set()
    for item in lst:
        if isinstance(item, dict):
            result.add(_freeze_dict(item))
        elif isinstance(item, list):
            result.add(_freeze_list(item))
        else:
            result.add(item)
    return result


def _freeze_dict(d: dict) -> Any:
    """Recursively freeze a dictionary into a hashable tuple structure."""
    items = []
    for key in sorted(d.keys(), key=_sort_key):
        val = d[key]
        if isinstance(val, dict):
            items.append((key, _freeze_dict(val)))
        elif isinstance(val, list):
            items.append((key, _freeze_list(val)))
        else:
            items.append((key, val))
    return tuple(items)


def _freeze_list(lst: list) -> Any:
    """Recursively freeze a list into a hashable tuple structure."""
    items = []
    for item in lst:
        if isinstance(item, dict):
            items.append(_freeze_dict(item))
        elif isinstance(item, list):
            items.append(_freeze_list(item))
        else:
            items.append(item)
    return tuple(items)


def _sort_key(val: Any) -> Any:
    """Ensure consistent sorting of mixed-type keys."""
    return (type(val).__name__, val)
