"""Read-only Git and process inspection adapters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from gptlink.agent.filesystem.paths import SandboxPaths
from gptlink.common.types import Capability


class GitReadService:
    def __init__(
        self,
        paths: SandboxPaths,
        capabilities: set[Capability],
        *,
        max_output_bytes: int = 1_048_576,
        timeout: float = 30,
    ) -> None:
        self.paths = paths
        self.capabilities = capabilities
        self.max_output_bytes = max_output_bytes
        self.timeout = timeout

    def _repository(self, path: str | Path) -> Path:
        if Capability.GIT_READ not in self.capabilities:
            raise PermissionError("capability denied: git.read")
        repository = self.paths.resolve_existing(path)
        if not repository.is_dir():
            raise ValueError("Git path is not a directory")
        return repository

    async def _run(self, repository: Path, *arguments: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(repository),
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise ValueError("Git read timed out") from None
        if len(stdout) + len(stderr) > self.max_output_bytes:
            raise ValueError("Git output limit exceeded")
        if process.returncode != 0:
            raise ValueError("Git read failed")
        return stdout.decode("utf-8", errors="replace")

    async def status(self, path: str | Path) -> str:
        return await self._run(self._repository(path), "status", "--porcelain=v1")

    async def diff(self, path: str | Path, revision: str | None = None) -> str:
        arguments = ["diff", "--no-ext-diff", "--no-textconv"]
        if revision is not None:
            if revision.startswith("-") or len(revision) > 200:
                raise ValueError("invalid Git revision")
            arguments.extend([revision, "--"])
        return await self._run(self._repository(path), *arguments)


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    command: str


class ProcessReadService:
    def __init__(self, capabilities: set[Capability], *, max_results: int = 1_000) -> None:
        self.capabilities = capabilities
        self.max_results = max_results

    def list(self) -> list[ProcessInfo]:
        if Capability.PROCESS_READ not in self.capabilities:
            raise PermissionError("capability denied: process.read")
        processes: list[ProcessInfo] = []
        for entry in sorted(Path("/proc").iterdir(), key=lambda item: item.name):
            if len(processes) >= self.max_results or not entry.name.isdigit():
                continue
            try:
                raw = (entry / "cmdline").read_bytes().replace(b"\0", b" ").strip()
                if not raw:
                    raw = (entry / "comm").read_bytes().strip()
            except (OSError, PermissionError):
                continue
            command = raw.decode("utf-8", errors="replace")[:4096]
            if command:
                processes.append(ProcessInfo(pid=int(entry.name), command=command))
        return processes
