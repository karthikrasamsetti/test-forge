"""FastAPI server for test-forge — exposes the pipeline as a REST API."""

from __future__ import annotations

import structlog
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from test_forge.api.jobs import job_store, run_generation_job
from test_forge.api.models import (
    HealthResponse,
    JobCreatedResponse,
    JobResultResponse,
    JobStatus,
    JobStatusResponse,
    ScriptSummary,
)
from test_forge.config.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

# ------------------------------------------------------------------ #
# App                                                                  #
# ------------------------------------------------------------------ #

app = FastAPI(
    title="test-forge API",
    description="AI-powered test script generator — upload manual test cases, get Playwright scripts.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ #
# Routes                                                               #
# ------------------------------------------------------------------ #


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health() -> HealthResponse:
    """Liveness check — returns OK if the server is running."""
    return HealthResponse()


@app.post("/generate", response_model=JobCreatedResponse, tags=["Generation"])
async def generate(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Test case file (CSV, Excel, Word, PDF, MD)"),
    target_url: str = Form(..., description="URL of the application under test"),
    framework: str = Form(default="playwright", description="Test framework"),
    force_auth_refresh: bool = Form(default=False, description="Force re-login"),
) -> JobCreatedResponse:
    """Upload a test case file and start a generation job.

    Returns a job_id immediately. Poll GET /status/{job_id} to track progress.
    """
    # Validate framework
    if framework not in ("playwright",):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported framework '{framework}'. Choose: playwright",
        )

    # Validate file type
    allowed = {".csv", ".xlsx", ".xls", ".docx", ".pdf", ".md"}
    filename = file.filename or "upload.csv"
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {allowed}",
        )

    # Read file bytes
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    # Create job and kick off background task
    job = job_store.create()
    logger.info(
        "generate_request",
        job_id=job.job_id,
        url=target_url,
        file=filename,
        framework=framework,
    )

    background_tasks.add_task(
        run_generation_job,
        job_id=job.job_id,
        input_file_bytes=file_bytes,
        input_filename=filename,
        target_url=target_url,
        framework=framework,
        force_auth_refresh=force_auth_refresh,
        output_dir=settings.OUTPUT_DIR,
    )

    return JobCreatedResponse(job_id=job.job_id)


@app.get("/status/{job_id}", response_model=JobStatusResponse, tags=["Generation"])
async def get_status(job_id: str) -> JobStatusResponse:
    """Poll the status of a generation job."""
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    response = JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        error=job.error,
    )

    if job.result:
        response.elapsed_seconds = job.result.elapsed_seconds
        response.total_test_cases = job.result.total_test_cases
        response.total_generated = job.result.total_generated
        response.total_failed = job.result.total_failed
        response.warnings = job.result.warnings

    return response


@app.get("/scripts/{job_id}", response_model=JobResultResponse, tags=["Generation"])
async def get_scripts(job_id: str) -> JobResultResponse:
    """Get the full results of a completed generation job."""
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    if job.status == JobStatus.PENDING or job.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=202,
            detail=f"Job '{job_id}' is still {job.status.value} — try again later",
        )

    if job.status == JobStatus.FAILED or not job.result:
        return JobResultResponse(
            job_id=job.job_id,
            status=job.status,
            warnings=[job.error] if job.error else [],
        )

    result = job.result
    scripts = [
        ScriptSummary(
            test_id=s.test_id,
            filename=s.filename,
            success=s.success,
            warnings=s.warnings,
        )
        for s in result.scripts
    ]

    return JobResultResponse(
        job_id=job.job_id,
        status=job.status,
        scripts=scripts,
        output_files=result.output_files,
        elapsed_seconds=result.elapsed_seconds,
        warnings=result.warnings,
    )


@app.get("/jobs", tags=["System"])
async def list_jobs() -> list[dict]:
    """List all jobs and their current status (useful for debugging)."""
    return [{"job_id": j.job_id, "status": j.status.value} for j in job_store.all_jobs()]
