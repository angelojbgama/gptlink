"""Behavioral contracts for the common configuration boundary.

The tests deliberately exercise the public settings object: changing a safe
default, enum wire value, or validation branch must break one of these tests.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from gptlink.common.config import Settings
from gptlink.common.types import (
    ApprovalStatus,
    Capability,
    DeviceStatus,
    JobStatus,
    PermissionLevel,
    ShellKind,
    StreamKind,
)


def test_console_entry_points_are_importable_and_safe() -> None:
    """Installed console entry points can start before Task 6 adds commands."""
    from gptlink.agent.__main__ import app as agent_app
    from gptlink.cli.main import app as gateway_app

    assert callable(gateway_app)
    assert callable(agent_app)
    assert gateway_app() == 0
    assert agent_app() == 0


def test_settings_have_conservative_defaults() -> None:
    """A default deployment stays local, uses SQLite, and permits no roots."""
    settings = Settings()

    assert settings.env == "development"
    assert settings.gateway_host == "127.0.0.1"
    assert settings.gateway_port == 8000
    assert settings.database_url == "sqlite+aiosqlite:///./gptlink.db"
    assert settings.agent_roots == []
    assert settings.max_concurrent_jobs == 2
    assert settings.max_command_length == 4096
    assert settings.max_file_bytes == 1_048_576
    assert settings.max_search_results == 1_000
    assert settings.max_job_output_bytes == 1_048_576


def test_common_enums_have_stable_wire_values() -> None:
    """Protocol and persistence consumers receive the documented values."""
    assert {member.value for member in Capability} == {
        "filesystem.read",
        "filesystem.write",
        "filesystem.search",
        "command.start",
        "git.read",
        "process.read",
    }
    assert {member.value for member in PermissionLevel} == {
        "READ_ONLY",
        "READ_WRITE",
        "FULL_ACCESS",
    }
    assert {member.value for member in DeviceStatus} == {
        "ONLINE",
        "OFFLINE",
        "BUSY",
        "DEGRADED",
        "REVOKED",
    }
    assert {member.value for member in JobStatus} == {
        "PENDING",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
        "TIMED_OUT",
    }
    assert {member.value for member in ApprovalStatus} == {
        "PENDING",
        "APPROVED",
        "REJECTED",
        "EXPIRED",
    }
    assert {member.value for member in ShellKind} == {"bash", "powershell", "cmd", "wsl"}
    assert {member.value for member in StreamKind} == {"stdout", "stderr"}


def test_production_rejects_non_tls_gateway_url() -> None:
    """A production Agent cannot be pointed at a cleartext Gateway."""
    with pytest.raises(ValidationError, match="HTTPS or WSS"):
        Settings(env="production", gateway_url="http://gateway.example.test")


def test_agent_root_cannot_be_filesystem_root() -> None:
    """An Agent policy never grants access to the entire host filesystem."""
    with pytest.raises(ValidationError, match="must not include /"):
        Settings(agent_roots=[Path("/")])


def test_agent_root_cannot_be_windows_filesystem_root() -> None:
    """The root policy rejects `C:\\` even when tests run on POSIX."""
    with pytest.raises(ValidationError, match=r"must not include / or C:\\\\"):
        Settings(agent_roots=[Path(r"C:\\")])
