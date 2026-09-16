"""Bounded asynchronous jobs and real Linux process cancellation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

from gptlink.common.types import JobStatus, ShellKind

from .executors.base import JobResult, OutputChunk
from .executors.linux import LinuxExecutor


@dataclass
class _Job:
    result: JobResult
    task: asyncio.Task[JobResult]
    chunks: list[OutputChunk] = field(default_factory=list)
    process: asyncio.subprocess.Process | None = None
    cancel_requested: bool = False
    output_bytes: int = 0


class JobManager:
    def __init__(self, *, max_output_bytes: int = 1_048_576, max_concurrent_jobs: int = 2) -> None:
        self.max_output_bytes = max_output_bytes
        self.semaphore = asyncio.Semaphore(max_concurrent_jobs)
        self.executor = LinuxExecutor()
        self._jobs: dict[UUID, _Job] = {}

    async def start(
        self,
        *,
        device_id: UUID,
        request_id: UUID,
        command: str,
        shell: ShellKind,
        cwd: Path,
        timeout: float,
    ) -> UUID:
        if not command or timeout <= 0:
            raise ValueError("command and positive timeout are required")
        job_id = uuid4()
        placeholder = JobResult(job_id, JobStatus.PENDING, None, False)
        task = asyncio.create_task(
            self._run(job_id, command, shell, cwd, timeout), name=f"gptlink-job-{job_id}"
        )
        self._jobs[job_id] = _Job(placeholder, task)
        return job_id

    async def _run(
        self, job_id: UUID, command: str, shell: ShellKind, cwd: Path, timeout: float
    ) -> JobResult:
        job = self._jobs[job_id]
        async with self.semaphore:
            try:
                process_task = asyncio.create_task(
                    self._execute(job, command, shell, cwd), name=f"gptlink-process-{job_id}"
                )
                try:
                    result = await asyncio.wait_for(process_task, timeout=timeout)
                except TimeoutError:
                    await self._terminate(job)
                    process_task.cancel()
                    await asyncio.gather(process_task, return_exceptions=True)
                    result = JobResult(
                        job_id, JobStatus.TIMED_OUT, None, job.result.output_truncated
                    )
                except asyncio.CancelledError:
                    await self._terminate(job)
                    raise
            except asyncio.CancelledError:
                result = JobResult(job_id, JobStatus.CANCELLED, None, job.result.output_truncated)
            except Exception:
                result = JobResult(job_id, JobStatus.FAILED, None, job.result.output_truncated)
        job.result = result
        return result

    async def _execute(self, job: _Job, command: str, shell: ShellKind, cwd: Path) -> JobResult:
        async def on_chunk(chunk: OutputChunk) -> bool:
            encoded_size = len(chunk.data.encode("utf-8"))
            if job.output_bytes + encoded_size > self.max_output_bytes:
                job.result = JobResult(
                    job.result.job_id, job.result.status, job.result.exit_code, True
                )
                return False
            job.output_bytes += encoded_size
            job.chunks.append(chunk)
            return True

        # LinuxExecutor owns the actual process, while this callback keeps the
        # process group available for cancellation once it is created.
        async def invoke():
            return await self.executor.run(
                job.result.job_id,
                command,
                shell,
                cwd,
                on_chunk,
                on_process=lambda process: setattr(job, "process", process),
            )

        code, truncated = await invoke()
        return JobResult(
            job.result.job_id,
            JobStatus.SUCCEEDED if code == 0 else JobStatus.FAILED,
            code,
            truncated or job.result.output_truncated,
        )

    async def _terminate(self, job: _Job) -> None:
        if job.process is not None:
            await self.executor.terminate(job.process)

    async def wait(self, job_id: UUID) -> JobResult:
        return await self._jobs[job_id].task

    async def status(self, job_id: UUID) -> JobResult:
        return self._jobs[job_id].result

    async def output(self, job_id: UUID, *, after_sequence: int = -1) -> list[OutputChunk]:
        return [
            chunk for chunk in self._jobs[job_id].chunks if chunk.sequence_number > after_sequence
        ]

    async def cancel(self, job_id: UUID) -> bool:
        job = self._jobs[job_id]
        if job.task.done():
            return False
        job.cancel_requested = True
        await self._terminate(job)
        job.task.cancel()
        return True
