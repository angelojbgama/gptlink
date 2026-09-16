"""Explicit Bash executor with process-group cancellation."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID

from gptlink.common.types import ShellKind, StreamKind
from gptlink.persistence.models import utc_now

from .base import OutputChunk

ChunkCallback = Callable[[OutputChunk], Awaitable[bool | None]]


def _start_process_group() -> None:
    setsid = getattr(os, "setsid", None)
    if setsid is None:
        raise RuntimeError("POSIX process groups are unavailable")
    setsid()


def _signal_process_group(pid: int, signum: int) -> None:
    killpg = getattr(os, "killpg", None)
    if killpg is None:
        raise RuntimeError("POSIX process groups are unavailable")
    killpg(pid, signum)


class LinuxExecutor:
    async def run(
        self,
        job_id: UUID,
        command: str,
        shell: ShellKind,
        cwd: Path | str,
        on_chunk: ChunkCallback,
        on_process: Callable[[asyncio.subprocess.Process], None] | None = None,
    ) -> tuple[int | None, bool]:
        if shell is not ShellKind.BASH:
            raise ValueError("Linux Agent accepts shell=linux/bash only")
        process = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "-lc",
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=_start_process_group,
        )
        if on_process is not None:
            on_process(process)
        sequence = 0
        truncated = False
        lock = asyncio.Lock()

        async def read(stream, kind: StreamKind) -> None:
            nonlocal sequence, truncated
            while True:
                data = await stream.read(4096)
                if not data:
                    return
                async with lock:
                    chunk = OutputChunk(
                        job_id=job_id,
                        sequence_number=sequence,
                        stream=kind,
                        timestamp=utc_now(),
                        data=data.decode("utf-8", errors="replace"),
                    )
                    sequence += 1
                if await on_chunk(chunk) is False:
                    truncated = True

        await asyncio.gather(
            read(process.stdout, StreamKind.STDOUT),
            read(process.stderr, StreamKind.STDERR),
            process.wait(),
        )
        return process.returncode, truncated

    @staticmethod
    async def terminate(process: asyncio.subprocess.Process, grace: float = 0.5) -> None:
        if process.returncode is not None:
            return
        try:
            _signal_process_group(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=grace)
        except TimeoutError:
            try:
                _signal_process_group(process.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            except ProcessLookupError:
                return
            await process.wait()
