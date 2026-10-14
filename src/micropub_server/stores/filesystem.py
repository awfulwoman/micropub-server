"""Local file-system store for development and tests."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .base import Change, Delete, Move, Put, StoreError


def _umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current


class FileSystemStore:
    name = "File system"

    def __init__(self, directory: str | Path):
        self.root = Path(directory).resolve()
        self.uid = f"file://{self.root}"

    def _path(self, path: str) -> Path:
        full = (self.root / path.lstrip("/")).resolve()
        if self.root != full and self.root not in full.parents:
            raise StoreError(f"path escapes the store: {path}")
        return full

    async def exists(self, path: str) -> bool:
        return self._path(path).is_file()

    async def read(self, path: str) -> bytes:
        try:
            return self._path(path).read_bytes()
        except FileNotFoundError as exc:
            raise StoreError(f"not found: {path}", 404) from exc

    async def commit(self, changes: list[Change], message: str) -> None:
        for change in changes:
            if isinstance(change, Put):
                self._write(change.path, change.content)
            elif isinstance(change, Delete):
                self._delete(change.path)
            elif isinstance(change, Move):
                content = (
                    change.content
                    if change.content is not None
                    else await self.read(change.old_path)
                )
                self._write(change.new_path, content)
                self._delete(change.old_path)

    def _write(self, path: str, content: bytes) -> None:
        target = self._path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
            # mkstemp makes the file 0600; give it the mode a normal write would get
            os.chmod(tmp, 0o666 & ~_umask())
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _delete(self, path: str) -> None:
        target = self._path(path)
        try:
            target.unlink()
        except FileNotFoundError as exc:
            raise StoreError(f"not found: {path}", 404) from exc
        parent = target.parent
        while parent != self.root and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent

    async def check(self) -> str | None:
        if not self.root.is_dir():
            return f"directory does not exist: {self.root}"
        if not os.access(self.root, os.W_OK):
            return f"directory is not writable: {self.root}"
        return None
