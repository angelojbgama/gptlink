"""Canonical root containment checks for Agent filesystem access."""

from pathlib import Path


class SandboxError(ValueError):
    """A path is outside every configured root or cannot be resolved safely."""


class SandboxPaths:
    def __init__(self, roots: list[Path]) -> None:
        if not roots:
            raise ValueError("at least one filesystem root is required")
        self.roots = tuple(self._root(root) for root in roots)

    @staticmethod
    def _root(root: Path) -> Path:
        resolved = Path(root).expanduser().resolve(strict=True)
        if not resolved.is_dir():
            raise SandboxError("filesystem root must be a directory")
        if resolved == Path(resolved.anchor):
            raise SandboxError("universal filesystem roots are not allowed")
        return resolved

    def resolve(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            raise SandboxError("filesystem paths must be absolute")
        resolved = candidate.resolve(strict=False)
        for root in self.roots:
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            return resolved
        raise SandboxError("path is outside an allowed filesystem root")

    def resolve_existing(self, path: str | Path) -> Path:
        resolved = self.resolve(path)
        if not resolved.exists():
            raise SandboxError("path does not exist")
        return resolved
