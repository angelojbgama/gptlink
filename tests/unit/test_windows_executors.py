"""Windows executors preserve explicit shell and path namespaces."""

from pathlib import Path

import pytest

from gptlink.agent.executors.cmd import CmdExecutor
from gptlink.agent.executors.powershell import PowerShellExecutor
from gptlink.agent.executors.windows_process import WindowsJobObjectManager
from gptlink.agent.executors.wsl import WslExecutor


def test_powershell_and_cmd_build_explicit_argv():
    powershell = PowerShellExecutor(executable=r"C:\Windows\powershell.exe")
    cmd = CmdExecutor(executable=r"C:\Windows\cmd.exe")
    assert powershell.build_argv("Write-Output 'hello'") == [
        r"C:\Windows\powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Write-Output 'hello'",
    ]
    assert cmd.build_argv("echo hello") == [
        r"C:\Windows\cmd.exe",
        "/d",
        "/s",
        "/c",
        "echo hello",
    ]


def test_wsl_keeps_linux_namespace_and_never_converts_windows_paths():
    executor = WslExecutor(executable=r"C:\Windows\wsl.exe", allowed_roots=["/mnt/c/work"])
    assert executor.build_argv("pwd", "/mnt/c/work/project") == [
        r"C:\Windows\wsl.exe",
        "--cd",
        "/mnt/c/work/project",
        "--exec",
        "/bin/bash",
        "-lc",
        "pwd",
    ]
    with pytest.raises(ValueError, match="Linux absolute"):
        executor.build_argv("pwd", r"C:\work")
    with pytest.raises(ValueError, match="outside"):
        executor.build_argv("pwd", "/mnt/c/outside")


class FakeNativeJobs:
    def __init__(self):
        self.calls = []

    def create_and_assign(self, pid):
        self.calls.append(("assign", pid))
        return pid + 1000

    def terminate(self, handle):
        self.calls.append(("terminate", handle))

    def close(self, handle):
        self.calls.append(("close", handle))


def test_job_object_manager_terminates_and_closes_exact_assigned_job():
    native = FakeNativeJobs()
    manager = WindowsJobObjectManager(native=native)
    manager.assign(42)
    manager.terminate(42)
    assert native.calls == [("assign", 42), ("terminate", 1042), ("close", 1042)]


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows-only paths")
def test_windows_sandbox_is_case_insensitive_and_rejects_escape(tmp_path: Path):
    from gptlink.agent.filesystem.paths import SandboxError, SandboxPaths

    root = tmp_path / "Allowed" / "Project"
    root.mkdir(parents=True)
    sandbox = SandboxPaths([root])
    assert sandbox.resolve(str(root).swapcase() + r"\file.txt").parent == root
    with pytest.raises(SandboxError):
        sandbox.resolve(root / ".." / "outside.txt")
    with pytest.raises(SandboxError):
        sandbox.resolve(r"\\server\share\outside.txt")
