"""Shared executor result types."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from gptlink.common.types import JobStatus, StreamKind


@dataclass(frozen=True)
class OutputChunk:
    job_id: UUID
    sequence_number: int
    stream: StreamKind
    timestamp: datetime
    data: str


@dataclass(frozen=True)
class JobResult:
    job_id: UUID
    status: JobStatus
    exit_code: int | None
    output_truncated: bool
