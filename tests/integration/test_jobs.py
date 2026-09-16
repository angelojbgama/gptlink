"""Real Linux process-group jobs with bounded streamed output."""

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from gptlink.agent.jobs import JobManager
from gptlink.common.types import JobStatus, ShellKind, StreamKind


@pytest.mark.asyncio
async def test_job_streams_both_output_streams_and_finishes(tmp_path: Path):
    manager = JobManager(max_output_bytes=1024)
    job_id = await manager.start(
        device_id=uuid4(),
        request_id=uuid4(),
        command=(
            f"{sys.executable} -c \"import sys; print('out'); print('err', file=sys.stderr)\""
        ),
        shell=ShellKind.BASH,
        cwd=tmp_path,
        timeout=5,
    )
    result = await manager.wait(job_id)
    assert result.status is JobStatus.SUCCEEDED
    chunks = await manager.output(job_id)
    assert {chunk.stream for chunk in chunks} == {StreamKind.STDOUT, StreamKind.STDERR}
    assert [chunk.sequence_number for chunk in chunks] == list(range(len(chunks)))


@pytest.mark.asyncio
async def test_cancel_kills_process_group(tmp_path: Path):
    manager = JobManager(max_output_bytes=1024)
    job_id = await manager.start(
        device_id=uuid4(),
        request_id=uuid4(),
        command=f'{sys.executable} -c "import time; time.sleep(30)"',
        shell=ShellKind.BASH,
        cwd=tmp_path,
        timeout=30,
    )
    await asyncio.sleep(0.1)
    assert await manager.cancel(job_id) is True
    result = await manager.wait(job_id)
    assert result.status is JobStatus.CANCELLED


@pytest.mark.asyncio
async def test_timeout_is_a_terminal_real_kill(tmp_path: Path):
    manager = JobManager(max_output_bytes=1024)
    job_id = await manager.start(
        device_id=uuid4(),
        request_id=uuid4(),
        command=f'{sys.executable} -c "import time; time.sleep(30)"',
        shell=ShellKind.BASH,
        cwd=tmp_path,
        timeout=0.1,
    )
    result = await manager.wait(job_id)
    assert result.status is JobStatus.TIMED_OUT


@pytest.mark.asyncio
async def test_output_is_bounded(tmp_path: Path):
    manager = JobManager(max_output_bytes=5)
    job_id = await manager.start(
        device_id=uuid4(),
        request_id=uuid4(),
        command="printf 1234567890",
        shell=ShellKind.BASH,
        cwd=tmp_path,
        timeout=5,
    )
    result = await manager.wait(job_id)
    assert result.status is JobStatus.SUCCEEDED
    assert result.output_truncated is True
    assert sum(len(chunk.data.encode()) for chunk in await manager.output(job_id)) <= 5
