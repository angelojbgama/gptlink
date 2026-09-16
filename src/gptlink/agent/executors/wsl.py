"""Explicit WSL executor; Windows and Linux path namespaces stay distinct."""

import asyncio
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from uuid import UUID

from gptlink.common.types import ShellKind

from .windows_process import ChunkCallback, WindowsJobObjectManager, WindowsShellExecutor


def wsl_available(executable: str | None = None) -> bool:
    executable = executable or shutil.which("wsl.exe")
    if executable is None:
        return False
    try:
        result = subprocess.run(
            [executable, "--list", "--quiet"],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(result.stdout.replace(b"\x00", b"").strip())


class WslExecutor(WindowsShellExecutor):
    shell = ShellKind.WSL

    def __init__(
        self,
        *,
        executable: str | None = None,
        allowed_roots: list[str] | None = None,
        jobs: WindowsJobObjectManager | None = None,
    ) -> None:
        super().__init__(jobs=jobs)
        resolved = executable or shutil.which("wsl.exe")
        if resolved is None:
            raise RuntimeError("WSL is unavailable")
        self.executable = resolved
        self.allowed_roots = tuple(PurePosixPath(root) for root in (allowed_roots or []))

    def _cwd(self, cwd: str) -> str:
        if not cwd.startswith("/") or "\\" in cwd:
            raise ValueError("WSL cwd must be a Linux absolute path")
        candidate = PurePosixPath(cwd)
        if not any(
            candidate == root or candidate.is_relative_to(root) for root in self.allowed_roots
        ):
            raise ValueError("WSL cwd is outside an allowed root")
        return candidate.as_posix()

    def build_argv(self, command: str, cwd: str | None = None) -> list[str]:
        if cwd is None:
            raise ValueError("WSL cwd is required")
        return [
            self.executable,
            "--cd",
            self._cwd(cwd),
            "--exec",
            "/bin/bash",
            "-lc",
            command,
        ]

    async def _realpath(self, path: str) -> PurePosixPath:
        process = await asyncio.create_subprocess_exec(
            self.executable,
            "--exec",
            "/usr/bin/readlink",
            "-f",
            "--",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise ValueError("WSL path resolution timed out") from None
        resolved = stdout.decode("utf-8", errors="strict").strip()
        if process.returncode != 0 or not resolved.startswith("/"):
            raise ValueError("WSL cwd does not exist")
        return PurePosixPath(resolved)

    async def run(
        self,
        job_id: UUID,
        command: str,
        shell: ShellKind,
        cwd: Path | str,
        on_chunk: ChunkCallback,
        on_process=None,
    ) -> tuple[int | None, bool]:
        candidate = await self._realpath(self._cwd(str(cwd)))
        roots = [await self._realpath(root.as_posix()) for root in self.allowed_roots]
        if not any(candidate == root or candidate.is_relative_to(root) for root in roots):
            raise ValueError("WSL cwd resolves outside an allowed root")
        return await super().run(job_id, command, shell, candidate.as_posix(), on_chunk, on_process)

    def subprocess_cwd(self, cwd: Path | str) -> None:
        return None
