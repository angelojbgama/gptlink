"""Explicit PowerShell executor for Windows Agents."""

import shutil

from gptlink.common.types import ShellKind

from .windows_process import WindowsJobObjectManager, WindowsShellExecutor


class PowerShellExecutor(WindowsShellExecutor):
    shell = ShellKind.POWERSHELL

    def __init__(
        self, *, executable: str | None = None, jobs: WindowsJobObjectManager | None = None
    ) -> None:
        super().__init__(jobs=jobs)
        resolved = executable or shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if resolved is None:
            raise RuntimeError("PowerShell is unavailable")
        self.executable = resolved

    def build_argv(self, command: str, cwd: str | None = None) -> list[str]:
        return [self.executable, "-NoProfile", "-NonInteractive", "-Command", command]
