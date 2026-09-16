"""Explicit CMD executor for Windows Agents."""

import shutil

from gptlink.common.types import ShellKind

from .windows_process import WindowsJobObjectManager, WindowsShellExecutor


class CmdExecutor(WindowsShellExecutor):
    shell = ShellKind.CMD

    def __init__(
        self, *, executable: str | None = None, jobs: WindowsJobObjectManager | None = None
    ) -> None:
        super().__init__(jobs=jobs)
        resolved = executable or shutil.which("cmd.exe")
        if resolved is None:
            raise RuntimeError("CMD is unavailable")
        self.executable = resolved

    def build_argv(self, command: str, cwd: str | None = None) -> list[str]:
        # /d disables AutoRun; /s applies CMD's documented /c quote handling.
        return [self.executable, "/d", "/s", "/c", command]
