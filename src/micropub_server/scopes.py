"""Micropub scope checks (spec 03)."""

from __future__ import annotations


def check_scope(scope: str | None, action: str = "create") -> bool | str:
    """Return False, True, or "draft" (allowed, but only as a draft)."""
    scopes = set((scope or "").split())
    needed = "create" if action == "undelete" else action
    if "draft" in scopes and needed in {"create", "update"}:
        return "draft"
    if needed in scopes:
        return True
    return needed == "create" and "post" in scopes  # deprecated alias
