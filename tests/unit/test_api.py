"""Unit tests for the test-forge FastAPI server."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from test_forge.api.jobs import JobStore, job_store
from test_forge.api.models import JobStatus
from test_forge.api.server import app
from test_forge.frameworks.base_template import GeneratedScript
from test_forge.orchestrator import OrchestratorResult

# ------------------------------------------------------------------ #
# Fixtures                                                             #
# ------------------------------------------------------------------ #


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def sample_csv_bytes() -> bytes:
    return b"ID,Description,Steps,Expected Result,Preconditions,Priority,Category,URL\nTC001,Login,1. Go to login,User logged in,None,high,auth,https://example.com\n"


@pytest.fixture
def mock_result() -> OrchestratorResult:
    result = OrchestratorResult(
        input_file="test.csv",
        target_url="https://example.com",
        framework="playwright",
    )
    result.total_test_cases = 1
    result.total_generated = 1
    result.total_failed = 0
    result.elapsed_seconds = 5.0
    result.scripts = [
        GeneratedScript(
            test_id="TC001",
            framework="playwright",
            filename="test_tc001_login.py",
            content="def test_tc001(): pass",
            success=True,
        )
    ]
    return result


# ------------------------------------------------------------------ #
# Health endpoint                                                      #
# ------------------------------------------------------------------ #


class TestHealth:
    def test_health_returns_200(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_ok(self, client: TestClient) -> None:
        data = client.get("/health").json()
        assert data["status"] == "ok"

    def test_health_returns_version(self, client: TestClient) -> None:
        data = client.get("/health").json()
        assert "version" in data


# ------------------------------------------------------------------ #
# POST /generate                                                       #
# ------------------------------------------------------------------ #


class TestGenerate:
    def test_generate_returns_202_with_job_id(
        self, client: TestClient, sample_csv_bytes: bytes
    ) -> None:
        response = client.post(
            "/generate",
            data={"target_url": "https://example.com"},
            files={"file": ("test.csv", sample_csv_bytes, "text/csv")},
        )
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "pending"

    def test_generate_starts_background_task(
        self, client: TestClient, sample_csv_bytes: bytes
    ) -> None:
        with patch("test_forge.api.server.run_generation_job") as mock_run:
            client.post(
                "/generate",
                data={"target_url": "https://example.com"},
                files={"file": ("test.csv", sample_csv_bytes, "text/csv")},
            )
            mock_run.assert_called_once()

    def test_generate_rejects_unsupported_framework(
        self, client: TestClient, sample_csv_bytes: bytes
    ) -> None:
        response = client.post(
            "/generate",
            data={"target_url": "https://example.com", "framework": "selenium"},
            files={"file": ("test.csv", sample_csv_bytes, "text/csv")},
        )
        assert response.status_code == 400

    def test_generate_rejects_unsupported_file_type(self, client: TestClient) -> None:
        response = client.post(
            "/generate",
            data={"target_url": "https://example.com"},
            files={"file": ("test.txt", b"some text", "text/plain")},
        )
        assert response.status_code == 400

    def test_generate_rejects_empty_file(self, client: TestClient) -> None:
        response = client.post(
            "/generate",
            data={"target_url": "https://example.com"},
            files={"file": ("test.csv", b"", "text/csv")},
        )
        assert response.status_code == 400

    def test_generate_accepts_excel_file(self, client: TestClient) -> None:
        response = client.post(
            "/generate",
            data={"target_url": "https://example.com"},
            files={
                "file": (
                    "test.xlsx",
                    b"fake excel bytes",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        # Should accept the file (even if pipeline fails later)
        assert response.status_code == 200

    def test_generate_accepts_markdown_file(self, client: TestClient) -> None:
        response = client.post(
            "/generate",
            data={"target_url": "https://example.com"},
            files={"file": ("test.md", b"# Tests", "text/markdown")},
        )
        assert response.status_code == 200


# ------------------------------------------------------------------ #
# GET /status/{job_id}                                                 #
# ------------------------------------------------------------------ #


class TestStatus:
    def test_status_returns_404_for_unknown_job(self, client: TestClient) -> None:
        response = client.get("/status/nonexistent-job-id")
        assert response.status_code == 404

    def test_status_returns_pending_for_new_job(
        self, client: TestClient, sample_csv_bytes: bytes
    ) -> None:
        with patch("test_forge.api.server.run_generation_job"):
            create = client.post(
                "/generate",
                data={"target_url": "https://example.com"},
                files={"file": ("test.csv", sample_csv_bytes, "text/csv")},
            )
        job_id = create.json()["job_id"]
        status = client.get(f"/status/{job_id}").json()
        assert status["status"] in ("pending", "running")

    def test_status_returns_done_for_completed_job(
        self, client: TestClient, mock_result: OrchestratorResult
    ) -> None:
        job = job_store.create()
        job_store.update_result(job.job_id, mock_result)

        response = client.get(f"/status/{job.job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "done"
        assert data["total_generated"] == 1

    def test_status_returns_failed_for_failed_job(self, client: TestClient) -> None:
        job = job_store.create()
        job_store.update_error(job.job_id, "Pipeline crashed")

        response = client.get(f"/status/{job.job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert "Pipeline crashed" in data["error"]


# ------------------------------------------------------------------ #
# GET /scripts/{job_id}                                                #
# ------------------------------------------------------------------ #


class TestScripts:
    def test_scripts_returns_404_for_unknown_job(self, client: TestClient) -> None:
        response = client.get("/scripts/nonexistent")
        assert response.status_code == 404

    def test_scripts_returns_202_while_running(self, client: TestClient) -> None:
        job = job_store.create()
        job_store.update_status(job.job_id, JobStatus.RUNNING)

        response = client.get(f"/scripts/{job.job_id}")
        assert response.status_code == 202

    def test_scripts_returns_results_when_done(
        self, client: TestClient, mock_result: OrchestratorResult
    ) -> None:
        job = job_store.create()
        job_store.update_result(job.job_id, mock_result)

        response = client.get(f"/scripts/{job.job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "done"
        assert len(data["scripts"]) == 1
        assert data["scripts"][0]["test_id"] == "TC001"

    def test_scripts_returns_output_files(
        self, client: TestClient, mock_result: OrchestratorResult
    ) -> None:
        job = job_store.create()
        job_store.update_result(job.job_id, mock_result)

        data = client.get(f"/scripts/{job.job_id}").json()
        assert "test_tc001_login.py" in data["output_files"]

    def test_scripts_returns_error_for_failed_job(self, client: TestClient) -> None:
        job = job_store.create()
        job_store.update_error(job.job_id, "Something went wrong")

        response = client.get(f"/scripts/{job.job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"


# ------------------------------------------------------------------ #
# GET /jobs                                                            #
# ------------------------------------------------------------------ #


class TestListJobs:
    def test_list_jobs_returns_200(self, client: TestClient) -> None:
        response = client.get("/jobs")
        assert response.status_code == 200

    def test_list_jobs_returns_list(self, client: TestClient) -> None:
        data = client.get("/jobs").json()
        assert isinstance(data, list)


# ------------------------------------------------------------------ #
# JobStore unit tests                                                  #
# ------------------------------------------------------------------ #


class TestJobStore:
    def test_create_returns_job(self) -> None:
        store = JobStore()
        job = store.create()
        assert job.job_id
        assert job.status == JobStatus.PENDING

    def test_get_returns_none_for_unknown(self) -> None:
        store = JobStore()
        assert store.get("unknown") is None

    def test_get_returns_created_job(self) -> None:
        store = JobStore()
        job = store.create()
        assert store.get(job.job_id) is job

    def test_update_status(self) -> None:
        store = JobStore()
        job = store.create()
        store.update_status(job.job_id, JobStatus.RUNNING)
        assert store.get(job.job_id).status == JobStatus.RUNNING  # type: ignore[union-attr]

    def test_update_result_sets_done(self, mock_result: OrchestratorResult) -> None:
        store = JobStore()
        job = store.create()
        store.update_result(job.job_id, mock_result)
        assert store.get(job.job_id).status == JobStatus.DONE  # type: ignore[union-attr]

    def test_update_error_sets_failed(self) -> None:
        store = JobStore()
        job = store.create()
        store.update_error(job.job_id, "boom")
        fetched = store.get(job.job_id)
        assert fetched.status == JobStatus.FAILED  # type: ignore[union-attr]
        assert fetched.error == "boom"  # type: ignore[union-attr]
