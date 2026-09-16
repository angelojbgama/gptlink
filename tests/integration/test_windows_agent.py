"""Real Windows shell, process, WSL, and process-tree integration evidence."""

import asyncio
import ctypes
import shutil
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path
from uuid import uuid4

import pytest

from gptlink.agent.client import CredentialStore
from gptlink.agent.executors.cmd import CmdExecutor
from gptlink.agent.executors.powershell import PowerShellExecutor
from gptlink.agent.executors.windows_process import WindowsProcessReadService
from gptlink.agent.executors.wsl import WslExecutor, wsl_available
from gptlink.agent.jobs import JobManager
from gptlink.agent.runtime import build_windows_agent
from gptlink.common.config import Settings
from gptlink.common.types import Capability, JobStatus, PermissionLevel, ShellKind

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only integration")


async def run_executor(executor, shell, command, cwd):
    chunks = []

    async def capture(chunk):
        chunks.append(chunk)
        return True

    code, truncated = await executor.run(uuid4(), command, shell, cwd, capture)
    return code, truncated, "".join(chunk.data for chunk in chunks)


def process_is_active(pid: int) -> bool:
    """Check execution state, rather than existence of a retained Windows handle."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == 259
    finally:
        kernel32.CloseHandle(handle)


@pytest.mark.asyncio
async def test_real_powershell_and_cmd(tmp_path: Path):
    power = await run_executor(
        PowerShellExecutor(), ShellKind.POWERSHELL, "Write-Output 'powershell-ok'", tmp_path
    )
    cmd = await run_executor(CmdExecutor(), ShellKind.CMD, "echo cmd-ok", tmp_path)
    assert power[0:2] == (0, False)
    assert cmd[0:2] == (0, False)
    assert "powershell-ok" in power[2]
    assert "cmd-ok" in cmd[2]


@pytest.mark.asyncio
@pytest.mark.skipif(not wsl_available(), reason="WSL distribution unavailable")
async def test_real_wsl_keeps_linux_cwd_namespace():
    result = await run_executor(WslExecutor(allowed_roots=["/tmp"]), ShellKind.WSL, "pwd", "/tmp")
    assert result[0:2] == (0, False)
    assert result[2].strip() == "/tmp"


@pytest.mark.asyncio
@pytest.mark.skipif(not wsl_available(), reason="WSL distribution unavailable")
async def test_real_wsl_rejects_symlink_escape():
    executable = shutil.which("wsl.exe")
    assert executable is not None
    base = f"/tmp/gptlink-test-{uuid4()}"
    root = f"{base}/root"
    link = f"{root}/outside"
    subprocess.run([executable, "--exec", "/bin/mkdir", "-p", root], check=True)
    subprocess.run([executable, "--exec", "/bin/ln", "-s", "/", link], check=True)
    try:
        with pytest.raises(ValueError, match="resolves outside"):
            await run_executor(WslExecutor(allowed_roots=[root]), ShellKind.WSL, "pwd", link)
    finally:
        subprocess.run([executable, "--exec", "/bin/rm", "-f", link], check=True)
        subprocess.run([executable, "--exec", "/bin/rmdir", root], check=True)
        subprocess.run([executable, "--exec", "/bin/rmdir", base], check=True)


def test_real_windows_process_list_is_bounded():
    service = WindowsProcessReadService({Capability.PROCESS_READ}, max_results=20)
    processes = service.list()
    assert 1 <= len(processes) <= 20
    assert all(process.pid > 0 and process.command for process in processes)


def test_windows_agent_announces_only_available_shells(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        agent_roots=[tmp_path],
        agent_wsl_roots=["/tmp"],
        agent_permission_level=PermissionLevel.READ_WRITE,
    )
    client = build_windows_agent(settings, CredentialStore(tmp_path / "credential.json"))
    assert {
        Capability.FILESYSTEM_READ,
        Capability.FILESYSTEM_WRITE,
        Capability.COMMAND_START,
        Capability.SHELL_POWERSHELL,
        Capability.SHELL_CMD,
    } <= client.capabilities
    assert (Capability.SHELL_WSL in client.capabilities) is wsl_available()


@pytest.mark.asyncio
async def test_cancel_terminates_powershell_parent_and_child(tmp_path: Path):
    manager = JobManager(executor=PowerShellExecutor(), max_output_bytes=4096)
    command = (
        "$child = Start-Process -FilePath $env:ComSpec "
        "-ArgumentList '/d','/c','ping -n 30 127.0.0.1 > nul' -PassThru; "
        "Write-Output $child.Id; Start-Sleep -Seconds 30"
    )
    job_id = await manager.start(
        device_id=uuid4(),
        request_id=uuid4(),
        command=command,
        shell=ShellKind.POWERSHELL,
        cwd=tmp_path,
        timeout=60,
    )
    child_pid = None
    for _ in range(100):
        output = "".join(chunk.data for chunk in await manager.output(job_id)).strip()
        if output.isdigit():
            child_pid = int(output)
            break
        await asyncio.sleep(0.05)
    assert child_pid is not None
    parent = manager._jobs[job_id].process
    assert parent is not None
    parent_pid = parent.pid
    assert await manager.cancel(job_id) is True
    assert (await manager.wait(job_id)).status is JobStatus.CANCELLED
    await asyncio.sleep(0.2)
    for pid in (parent_pid, child_pid):
        assert not process_is_active(pid)
