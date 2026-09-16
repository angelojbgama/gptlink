"""Bounded, non-destructive operations over canonical sandbox paths."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from heapq import nsmallest
from pathlib import Path

from gptlink.common.types import Capability, PermissionLevel

from .paths import SandboxPaths


@dataclass(frozen=True)
class FilesystemEntry:
    name: str
    path: str
    kind: str
    size: int


class FilesystemOperations:
    def __init__(
        self,
        paths: SandboxPaths,
        *,
        permission: PermissionLevel,
        capabilities: set[Capability],
        max_read_bytes: int,
        max_write_bytes: int,
        max_search_results: int,
    ) -> None:
        self.paths = paths
        self.permission = permission
        self.capabilities = capabilities
        self.max_read_bytes = max_read_bytes
        self.max_write_bytes = max_write_bytes
        self.max_search_results = max_search_results

    def _require(self, capability: Capability) -> None:
        if capability not in self.capabilities:
            raise PermissionError(f"capability denied: {capability.value}")

    def list(self, path: str | Path) -> list[FilesystemEntry]:
        self._require(Capability.FILESYSTEM_READ)
        directory = self.paths.resolve_existing(path)
        if not directory.is_dir():
            raise ValueError("path is not a directory")
        entries: list[FilesystemEntry] = []
        children = nsmallest(
            self.max_search_results,
            directory.iterdir(),
            key=lambda item: item.name.casefold(),
        )
        for child in children:
            resolved = self.paths.resolve_existing(child)
            stat = resolved.stat()
            entries.append(
                FilesystemEntry(
                    name=child.name,
                    path=str(resolved),
                    kind="directory" if resolved.is_dir() else "file",
                    size=stat.st_size if resolved.is_file() else 0,
                )
            )
        return entries

    def read(self, path: str | Path) -> str:
        self._require(Capability.FILESYSTEM_READ)
        target = self.paths.resolve_existing(path)
        if not target.is_file():
            raise ValueError("path is not a file")
        if target.stat().st_size > self.max_read_bytes:
            raise ValueError("file read limit exceeded")
        return target.read_text(encoding="utf-8")

    def write(self, path: str | Path, data: str) -> None:
        self._require(Capability.FILESYSTEM_WRITE)
        if self.permission is PermissionLevel.READ_ONLY:
            raise PermissionError("READ_ONLY policy forbids filesystem.write")
        encoded = data.encode("utf-8")
        if len(encoded) > self.max_write_bytes:
            raise ValueError("file write limit exceeded")
        target = self.paths.resolve(path)
        parent = self.paths.resolve(target.parent)
        if not parent.is_dir():
            raise ValueError("parent directory does not exist")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb", dir=parent, prefix=f".{target.name}.", delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def search(self, path: str | Path, query: str) -> list[FilesystemEntry]:
        self._require(Capability.FILESYSTEM_SEARCH)
        root = self.paths.resolve_existing(path)
        if not root.is_dir():
            raise ValueError("search path is not a directory")
        matches: list[FilesystemEntry] = []
        for candidate in root.rglob("*"):
            if len(matches) >= self.max_search_results:
                break
            try:
                resolved = self.paths.resolve_existing(candidate)
            except (ValueError, OSError):
                continue
            if not resolved.is_file() or resolved.stat().st_size > self.max_read_bytes:
                continue
            try:
                content = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if query in content:
                matches.append(
                    FilesystemEntry(candidate.name, str(resolved), "file", resolved.stat().st_size)
                )
        return matches
