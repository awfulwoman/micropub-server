"""Store interface (spec 07)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class StoreError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Put:
    path: str
    content: bytes


@dataclass(frozen=True)
class Delete:
    path: str


@dataclass(frozen=True)
class Move:
    old_path: str
    new_path: str
    content: bytes | None = None  # None: carry the old content across


Change = Put | Delete | Move


class Store(Protocol):
    name: str
    uid: str

    async def exists(self, path: str) -> bool: ...
    async def read(self, path: str) -> bytes: ...
    async def list_files(self, prefix: str) -> list[str]:
        """Every file path under prefix, relative to the store root."""
        ...

    async def commit(self, changes: list[Change], message: str) -> str | None:
        """Apply the changes as one commit. Return its id, or None if the store has no such thing."""
        ...

    async def check(self) -> str | None:
        """Return None when healthy, else a human-readable problem."""
        ...
