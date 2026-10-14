"""Micropub update operations on a JF2 post: add, replace, delete (spec 03)."""

from __future__ import annotations

import copy
from typing import Any

from .jf2 import convert_value
from .util import to_list

# Never changed by a client, whatever the operation.
PROTECTED = frozenset({"uid", "url", "post-type", "type"})


class UpdateError(ValueError):
    pass


def _require_object(name: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise UpdateError(f"{name} must be an object")
    return value


def _require_lists(name: str, ops: dict[str, Any]) -> None:
    for key, values in ops.items():
        if not isinstance(values, list):
            raise UpdateError(f"{name}.{key} must be a list of values")


def _convert(key: str, values: list[Any]) -> list[Any]:
    return [convert_value(key, v) for v in values]


def apply_update(
    props: dict[str, Any],
    *,
    add: Any = None,
    replace: Any = None,
    delete: Any = None,
) -> dict[str, Any]:
    """Return a new properties dict with the operations applied: add, then replace, then delete."""
    out = copy.deepcopy(props)

    if add is not None:
        add = _require_object("add", add)
        _require_lists("add", add)
        for key, values in add.items():  # every key, not just the first
            if key in PROTECTED:
                continue
            new = _convert(key, values)
            out[key] = (
                [*to_list(out[key]), *new] if key in out else (new[0] if len(new) == 1 else new)
            )

    if replace is not None:
        replace = _require_object("replace", replace)
        _require_lists("replace", replace)
        for key, values in replace.items():
            if key in PROTECTED or not values:
                continue
            new = _convert(key, values)
            out[key] = new[0] if len(new) == 1 else new

    if delete is not None:
        if isinstance(delete, list):
            for key in delete:
                if key not in PROTECTED:
                    out.pop(key, None)
        elif isinstance(delete, dict):
            _require_lists("delete", delete)
            for key, values in delete.items():
                if key in PROTECTED or key not in out:
                    continue
                remaining = [v for v in to_list(out[key]) if v not in values]
                if remaining:
                    out[key] = remaining
                else:
                    del out[key]
        else:
            raise UpdateError("delete must be a list of property names or an object of values")

    return out
