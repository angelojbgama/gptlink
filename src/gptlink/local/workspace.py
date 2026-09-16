"""Freeze a current directory and route every path through the existing sandbox."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gptlink.agent.filesystem.paths import SandboxPaths


@dataclass(frozen=True)
class LocalWorkspace:
    root: Path
    paths: SandboxPaths

    @classmethod
    def from_cwd(cls, cwd: Path | None = None) -> LocalWorkspace:
        root = (cwd or Path.cwd()).expanduser().resolve(strict=True)
        return cls(root=root, paths=SandboxPaths([root]))

    def resolve(self, value: str | Path, *, existing: bool = False) -> Path:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        if existing:
            return self.paths.resolve_existing(candidate)
        return self.paths.resolve(candidate)
