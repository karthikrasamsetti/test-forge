"""Job store — tracks generation jobs and runs them in the background."""

from __future__ import annotations

import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import structlog

from test_forge.api.models import JobStatus
from test_forge.orchestrator import Orchestrator, OrchestratorResult

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------ #
# Job record                                                           #
# ------------------------------------------------------------------ #


@dataclass
class Job:
    """Holds the state of one generation job."""

    job_id: str
    status: JobStatus = JobStatus.PENDING
    result: OrchestratorResult | None = None
    error: str = ""

    @classmethod
    def create(cls) -> Job:
        return cls(job_id=str(uuid.uuid4()))


# ------------------------------------------------------------------ #
# In-memory store                                                      #
# ------------------------------------------------------------------ #


class JobStore:
    """Thread-safe in-memory store for generation jobs.

    For production, replace with Redis or a database backend.
    Jobs are kept in memory for the lifetime of the process.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._log = logger.bind(component="job_store")

    def create(self) -> Job:
        job = Job.create()
        self._jobs[job.job_id] = job
        self._log.info("job_created", job_id=job.job_id)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def update_status(self, job_id: str, status: JobStatus) -> None:
        if job := self._jobs.get(job_id):
            job.status = status
            self._log.info("job_status_updated", job_id=job_id, status=status)

    def update_result(self, job_id: str, result: OrchestratorResult) -> None:
        if job := self._jobs.get(job_id):
            job.result = result
            job.status = JobStatus.DONE if result.success else JobStatus.FAILED
            self._log.info(
                "job_completed",
                job_id=job_id,
                status=job.status,
                generated=result.total_generated,
            )

    def update_error(self, job_id: str, error: str) -> None:
        if job := self._jobs.get(job_id):
            job.status = JobStatus.FAILED
            job.error = error
            self._log.error("job_failed", job_id=job_id, error=error)

    def all_jobs(self) -> list[Job]:
        return list(self._jobs.values())


# Singleton store — shared across the application lifetime
job_store = JobStore()


# ------------------------------------------------------------------ #
# Background runner                                                    #
# ------------------------------------------------------------------ #


def run_generation_job(
    job_id: str,
    input_file_bytes: bytes,
    input_filename: str,
    target_url: str,
    framework: str,
    force_auth_refresh: bool,
    output_dir: str,
) -> None:
    """Run the full pipeline in a background thread.

    Writes the uploaded file to a temp location, runs the orchestrator,
    then updates the job store with the result.
    """
    log = logger.bind(job_id=job_id)
    job_store.update_status(job_id, JobStatus.RUNNING)
    log.info("job_started", url=target_url, file=input_filename)

    # Write uploaded bytes to a temp file
    suffix = Path(input_filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode="wb") as tmp:
        tmp.write(input_file_bytes)
        tmp_path = tmp.name

    try:
        orch = Orchestrator(
            framework=framework,
            output_dir=output_dir,
            use_auth=True,
        )
        result = orch.run(
            input_file=tmp_path,
            target_url=target_url,
            force_auth_refresh=force_auth_refresh,
        )
        job_store.update_result(job_id, result)
        log.info("job_done", generated=result.total_generated)

    except Exception as exc:  # noqa: BLE001
        error_msg = f"Pipeline failed: {exc}"
        job_store.update_error(job_id, error_msg)
        log.error("job_error", error=str(exc))

    finally:
        Path(tmp_path).unlink(missing_ok=True)
