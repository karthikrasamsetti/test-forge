"""Job store — tracks generation jobs and runs them in the background."""

from __future__ import annotations

import tempfile
import uuid
from dataclasses import dataclass, field
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


# ------------------------------------------------------------------ #
# Test run store                                                        #
# ------------------------------------------------------------------ #


@dataclass
class RunJob:
    """Holds the state of one pytest run job."""

    run_id: str
    job_id: str
    status: JobStatus = JobStatus.PENDING
    result: dict = field(default_factory=dict)
    error: str = ""

    @classmethod
    def create(cls, job_id: str) -> RunJob:
        return cls(run_id=str(uuid.uuid4()), job_id=job_id)


class RunStore:
    """In-memory store for test run jobs."""

    def __init__(self) -> None:
        self._runs: dict[str, RunJob] = {}

    def create(self, job_id: str) -> RunJob:
        run = RunJob.create(job_id)
        self._runs[run.run_id] = run
        return run

    def get(self, run_id: str) -> RunJob | None:
        return self._runs.get(run_id)

    def update(self, run_id: str, status: JobStatus, result: dict, error: str = "") -> None:
        if run := self._runs.get(run_id):
            run.status = status
            run.result = result
            run.error = error


run_store = RunStore()


# ------------------------------------------------------------------ #
# Pytest runner                                                         #
# ------------------------------------------------------------------ #


def run_tests_job(
    run_id: str,
    output_dir: str,
    framework: str,
) -> None:
    """Run pytest on generated scripts in a background thread."""
    import re
    import subprocess
    import time

    log = logger.bind(run_id=run_id)
    run_store.update(run_id, JobStatus.RUNNING, {})
    log.info("test_run_started", output_dir=output_dir)

    test_dir = str(Path(output_dir) / framework)
    start = time.monotonic()

    try:
        proc = subprocess.run(
            [
                "pytest",
                test_dir,
                "-v",
                "--no-cov",
                "--no-header",
                "--tb=short",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        elapsed = round(time.monotonic() - start, 2)
        stdout = proc.stdout + proc.stderr

        # Parse results from pytest output
        results = []
        passed = 0
        failed = 0

        for line in stdout.splitlines():
            # Match lines like: path/test_file.py::test_func[chromium] PASSED
            match = re.search(
                r"(test_[\w]+\.py)::(test_[\w]+)(?:\[.*?\])?\s+(PASSED|FAILED|ERROR)",
                line,
            )
            if match:
                filename = match.group(1)
                test_name = match.group(2)
                outcome = match.group(3)
                ok = outcome == "PASSED"
                if ok:
                    passed += 1
                else:
                    failed += 1
                results.append(
                    {
                        "test_id": test_name,
                        "filename": filename,
                        "passed": ok,
                        "error": "" if ok else outcome,
                    }
                )

        result = {
            "total": passed + failed,
            "passed": passed,
            "failed": failed,
            "elapsed_seconds": elapsed,
            "results": results,
            "stdout": stdout[-3000:],  # last 3000 chars to avoid huge payloads
        }

        status = JobStatus.DONE if proc.returncode == 0 else JobStatus.FAILED
        run_store.update(run_id, status, result)
        log.info("test_run_done", passed=passed, failed=failed)

    except subprocess.TimeoutExpired:
        run_store.update(run_id, JobStatus.FAILED, {}, "Pytest timed out after 300s")
        log.error("test_run_timeout")
    except Exception as exc:  # noqa: BLE001
        run_store.update(run_id, JobStatus.FAILED, {}, str(exc))
        log.error("test_run_error", error=str(exc))
