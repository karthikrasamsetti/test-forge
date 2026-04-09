"""Pydantic models for the test-forge API request and response schemas."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

# ------------------------------------------------------------------ #
# Job status enum                                                      #
# ------------------------------------------------------------------ #


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


# ------------------------------------------------------------------ #
# Request models                                                       #
# ------------------------------------------------------------------ #


class GenerateRequest(BaseModel):
    """Query parameters for the /generate endpoint."""

    target_url: str = Field(
        ...,
        description="URL of the application under test",
        examples=["https://www.saucedemo.com"],
    )
    framework: str = Field(
        default="playwright",
        description="Test framework to generate for",
        examples=["playwright"],
    )
    force_auth_refresh: bool = Field(
        default=False,
        description="Force re-login even if a valid session exists",
    )


# ------------------------------------------------------------------ #
# Response models                                                      #
# ------------------------------------------------------------------ #


class JobCreatedResponse(BaseModel):
    """Returned immediately when a generation job is accepted."""

    job_id: str
    status: JobStatus = JobStatus.PENDING
    message: str = "Job accepted — poll /status/{job_id} for updates"


class ScriptSummary(BaseModel):
    """Summary of one generated test script."""

    test_id: str
    filename: str
    success: bool
    warnings: list[str] = []


class JobStatusResponse(BaseModel):
    """Returned by GET /status/{job_id}."""

    job_id: str
    status: JobStatus
    elapsed_seconds: float = 0.0
    total_test_cases: int = 0
    total_generated: int = 0
    total_failed: int = 0
    warnings: list[str] = []
    error: str = ""


class JobResultResponse(BaseModel):
    """Returned by GET /scripts/{job_id} — full results with script contents."""

    job_id: str
    status: JobStatus
    scripts: list[ScriptSummary] = []
    output_files: list[str] = []
    elapsed_seconds: float = 0.0
    warnings: list[str] = []


class HealthResponse(BaseModel):
    """Returned by GET /health."""

    status: str = "ok"
    version: str = "0.1.0"
