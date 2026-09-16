"""Windows Job Objects and bounded process listing without shell interpolation."""

from __future__ import annotations

import asyncio
import csv
import ctypes
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from gptlink.common.types import Capability, ShellKind, StreamKind
from gptlink.persistence.models import utc_now

from .base import OutputChunk

ChunkCallback = Callable[[OutputChunk], Awaitable[bool | None]]


class _IOCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IOCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _NativeJobs:
    KILL_ON_JOB_CLOSE = 0x00002000
    EXTENDED_LIMIT_INFORMATION = 9
    PROCESS_TERMINATE = 0x0001
    PROCESS_SET_QUOTA = 0x0100

    def __init__(self) -> None:
        self.kernel32: Any
        if sys.platform != "win32":
            raise RuntimeError("Windows Job Objects are unavailable")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        self.kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel32.OpenProcess.restype = ctypes.c_void_p
        self.kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, wintypes.UINT]
        self.kernel32.TerminateJobObject.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self.kernel32.CloseHandle.restype = wintypes.BOOL

    @staticmethod
    def _error(operation: str) -> OSError:
        if sys.platform != "win32":
            raise RuntimeError("Windows Job Objects are unavailable")
        code = ctypes.get_last_error()
        return OSError(code, f"{operation} failed: {ctypes.FormatError(code)}")

    def create_and_assign(self, pid: int) -> int:
        job = self.kernel32.CreateJobObjectW(None, None)
        if not job:
            raise self._error("CreateJobObjectW")
        try:
            limits = _ExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = self.KILL_ON_JOB_CLOSE
            if not self.kernel32.SetInformationJobObject(
                job,
                self.EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                raise self._error("SetInformationJobObject")
            process = self.kernel32.OpenProcess(
                self.PROCESS_TERMINATE | self.PROCESS_SET_QUOTA, False, pid
            )
            if not process:
                raise self._error("OpenProcess")
            try:
                if not self.kernel32.AssignProcessToJobObject(job, process):
                    raise self._error("AssignProcessToJobObject")
            finally:
                self.kernel32.CloseHandle(process)
            return int(job)
        except Exception:
            self.kernel32.CloseHandle(job)
            raise

    def terminate(self, handle: int) -> None:
        if not self.kernel32.TerminateJobObject(handle, 1):
            raise self._error("TerminateJobObject")

    def close(self, handle: int) -> None:
        self.kernel32.CloseHandle(handle)


class WindowsJobObjectManager:
    """Own one non-breakaway Job Object for every spawned command tree."""

    def __init__(self, *, native=None) -> None:
        self.native = native or _NativeJobs()
        self._jobs: dict[int, int] = {}
        self._lock = threading.Lock()

    def assign(self, pid: int) -> None:
        handle = self.native.create_and_assign(pid)
        with self._lock:
            self._jobs[pid] = handle

    def terminate(self, pid: int) -> None:
        with self._lock:
            handle = self._jobs.pop(pid, None)
        if handle is not None:
            try:
                self.native.terminate(handle)
            finally:
                self.native.close(handle)

    def close(self, pid: int) -> None:
        with self._lock:
            handle = self._jobs.pop(pid, None)
        if handle is not None:
            self.native.close(handle)


class WindowsShellExecutor:
    shell: ShellKind

    def __init__(self, *, jobs: WindowsJobObjectManager | None = None) -> None:
        self._jobs = jobs

    @property
    def jobs(self) -> WindowsJobObjectManager:
        if self._jobs is None:
            self._jobs = WindowsJobObjectManager()
        return self._jobs

    def build_argv(self, command: str, cwd: str | None = None) -> list[str]:
        raise NotImplementedError

    def subprocess_cwd(self, cwd: Path | str) -> str | None:
        return str(cwd)

    async def run(
        self,
        job_id: UUID,
        command: str,
        shell,
        cwd: Path | str,
        on_chunk: ChunkCallback,
        on_process: Callable[[asyncio.subprocess.Process], None] | None = None,
    ) -> tuple[int | None, bool]:
        if shell is not self.shell:
            raise ValueError("executor does not match selected shell")
        process = await asyncio.create_subprocess_exec(
            *self.build_argv(command, str(cwd)),
            cwd=self.subprocess_cwd(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        try:
            self.jobs.assign(process.pid)
        except Exception:
            process.kill()
            await process.wait()
            raise
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

        try:
            await asyncio.gather(
                read(process.stdout, StreamKind.STDOUT),
                read(process.stderr, StreamKind.STDERR),
                process.wait(),
            )
            return process.returncode, truncated
        finally:
            self.jobs.close(process.pid)

    async def terminate(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            self.jobs.terminate(process.pid)
            await process.wait()


@dataclass(frozen=True)
class WindowsProcessInfo:
    pid: int
    command: str


class WindowsProcessReadService:
    def __init__(
        self,
        capabilities: set[Capability],
        *,
        max_results: int = 1_000,
        executable: str = "tasklist.exe",
    ) -> None:
        self.capabilities = capabilities
        self.max_results = max_results
        self.executable = executable

    def list(self) -> list[WindowsProcessInfo]:
        if Capability.PROCESS_READ not in self.capabilities:
            raise PermissionError("capability denied: process.read")
        completed = subprocess.run(
            [self.executable, "/fo", "csv", "/nh"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        results: list[WindowsProcessInfo] = []
        for row in csv.reader(completed.stdout.splitlines()):
            if len(results) >= self.max_results:
                break
            if len(row) < 2:
                continue
            try:
                pid = int(row[1])
            except ValueError:
                continue
            command = row[0][:4096]
            if pid <= 0 or not command:
                continue
            results.append(WindowsProcessInfo(pid=pid, command=command))
        return results


class WindowsExecutor:
    """Route an explicit shell to its dedicated Windows executor."""

    def __init__(self, *, wsl_roots: list[str] | None = None) -> None:
        from .cmd import CmdExecutor
        from .powershell import PowerShellExecutor
        from .wsl import WslExecutor, wsl_available

        self.jobs = WindowsJobObjectManager()
        executors: list[WindowsShellExecutor] = [CmdExecutor(jobs=self.jobs)]
        try:
            executors.append(PowerShellExecutor(jobs=self.jobs))
        except RuntimeError:
            pass
        if wsl_roots and wsl_available():
            executors.append(WslExecutor(allowed_roots=wsl_roots, jobs=self.jobs))
        self.executors = {executor.shell: executor for executor in executors}

    async def run(self, job_id, command, shell, cwd, on_chunk, on_process=None):
        try:
            executor = self.executors[shell]
        except KeyError:
            raise ValueError(f"shell is unavailable: {shell.value}") from None
        return await executor.run(job_id, command, shell, cwd, on_chunk, on_process)

    async def terminate(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            self.jobs.terminate(process.pid)
            await process.wait()

    @property
    def shells(self) -> set[ShellKind]:
        return set(self.executors)
