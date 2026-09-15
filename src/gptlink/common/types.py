"""Stable string enums shared across GPTLink boundaries."""

from enum import StrEnum


class Capability(StrEnum):
    """An operation class an Agent may expose."""

    FILESYSTEM_READ = "filesystem.read"
    FILESYSTEM_WRITE = "filesystem.write"
    FILESYSTEM_SEARCH = "filesystem.search"
    COMMAND_START = "command.start"
    GIT_READ = "git.read"
    PROCESS_READ = "process.read"


class PermissionLevel(StrEnum):
    """The maximum access a caller has for a Device."""

    READ_ONLY = "READ_ONLY"
    READ_WRITE = "READ_WRITE"
    FULL_ACCESS = "FULL_ACCESS"


class DeviceStatus(StrEnum):
    """Current connection and authorization state of a Device."""

    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    BUSY = "BUSY"
    DEGRADED = "DEGRADED"
    REVOKED = "REVOKED"


class JobStatus(StrEnum):
    """Lifecycle state of an asynchronous command job."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class ApprovalStatus(StrEnum):
    """Lifecycle state of a mutation approval."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ShellKind(StrEnum):
    """An explicitly selected command interpreter."""

    BASH = "bash"
    POWERSHELL = "powershell"
    CMD = "cmd"
    WSL = "wsl"


class StreamKind(StrEnum):
    """A command output stream."""

    STDOUT = "stdout"
    STDERR = "stderr"
