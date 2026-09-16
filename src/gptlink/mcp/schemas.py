"""Stable typed results exposed by GPTLink MCP tools."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from gptlink.common.types import Capability, DeviceStatus, JobStatus, PermissionLevel, StreamKind


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeviceInfo(ToolResult):
    device_id: UUID
    display_name: str
    platform: str
    status: DeviceStatus
    agent_version: str
    capabilities: list[Capability]
    last_seen: datetime | None
    permission_level: PermissionLevel


class LeaseInfo(ToolResult):
    lease_id: UUID
    device_id: UUID
    owner: str
    expires_at: datetime


class ReleaseResult(ToolResult):
    released: bool


class FilesystemEntry(ToolResult):
    name: str
    path: str
    kind: str
    size: int


class FileContent(ToolResult):
    content: str


class WriteResult(ToolResult):
    written: bool


class JobStarted(ToolResult):
    job_id: UUID


class JobState(ToolResult):
    job_id: UUID
    status: JobStatus
    exit_code: int | None
    output_truncated: bool


class JobOutput(ToolResult):
    job_id: UUID
    sequence_number: int
    stream: StreamKind
    timestamp: datetime
    data: str


class CancelResult(ToolResult):
    cancelled: bool


class GitOutput(ToolResult):
    output: str


class ProcessInfo(ToolResult):
    pid: int
    command: str
