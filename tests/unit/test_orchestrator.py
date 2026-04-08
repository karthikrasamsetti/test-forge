"""Unit tests for Orchestrator — all four agents are fully mocked."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from test_forge.core.models import CrawlResult, TestCase
from test_forge.frameworks.base_template import GeneratedScript
from test_forge.orchestrator import Orchestrator, OrchestratorResult

# ------------------------------------------------------------------ #
# Shared test data                                                     #
# ------------------------------------------------------------------ #

SAMPLE_TEST_CASES = [
    TestCase(
        id="TC001",
        description="Login with valid credentials",
        steps=["Go to login page", "Enter credentials", "Click login"],
        expected_result="Redirected to inventory",
        priority="high",
    ),
    TestCase(
        id="TC002",
        description="Login with locked out user",
        steps=["Go to login page", "Enter locked credentials", "Click login"],
        expected_result="Error message shown",
        priority="high",
    ),
]

SAMPLE_SCRIPTS = [
    GeneratedScript(
        test_id="TC001",
        framework="playwright",
        filename="test_tc001_login_with_valid_credentials.py",
        content="def test_tc001(): pass",
        success=True,
    ),
    GeneratedScript(
        test_id="TC002",
        framework="playwright",
        filename="test_tc002_login_with_locked_out_user.py",
        content="def test_tc002(): pass",
        success=True,
    ),
]


# ------------------------------------------------------------------ #
# Factory                                                              #
# ------------------------------------------------------------------ #


def _make_orchestrator(
    tmp_path: Path,
    *,
    read_test_cases: list[TestCase] | None = None,
    crawl_success: bool = True,
    auth_session: str = "/fake/session.json",
    use_auth: bool = True,
    generate_scripts: list[GeneratedScript] | None = None,
) -> tuple[Orchestrator, dict[str, MagicMock]]:
    """Return an Orchestrator with all agents replaced by mocks."""
    read_result = MagicMock()
    read_result.test_cases = read_test_cases if read_test_cases is not None else SAMPLE_TEST_CASES
    read_result.warnings = []
    read_result.total_parsed = len(read_result.test_cases)

    crawl_result = MagicMock(spec=CrawlResult)
    crawl_result.url = "https://www.saucedemo.com"
    crawl_result.elements = []
    crawl_result.warnings = []
    crawl_result.success = crawl_success

    scripts = generate_scripts if generate_scripts is not None else SAMPLE_SCRIPTS

    mocks: dict[str, MagicMock] = {
        "reader": MagicMock(),
        "auth": MagicMock(),
        "crawler": MagicMock(),
        "generator": MagicMock(),
    }
    mocks["reader"].run.return_value = read_result
    mocks["auth"].authenticate_from_settings.return_value = auth_session
    mocks["crawler"].run.return_value = crawl_result
    mocks["generator"].generate_batch.return_value = scripts

    settings = MagicMock()
    settings.APP_USERNAME = "standard_user"
    settings.APP_PASSWORD = "secret_sauce"

    with patch("test_forge.orchestrator.get_settings", return_value=settings):
        orch = Orchestrator(
            framework="playwright",
            output_dir=str(tmp_path),
            use_auth=use_auth,
        )

    # Inject mocks directly
    orch._reader = mocks["reader"]
    orch._auth = mocks["auth"]
    orch._crawler = mocks["crawler"]
    orch._generator = mocks["generator"]
    orch._settings = settings

    return orch, mocks


# ------------------------------------------------------------------ #
# OrchestratorResult model                                             #
# ------------------------------------------------------------------ #


class TestOrchestratorResult:
    def test_success_true_when_no_failures(self) -> None:
        result = OrchestratorResult(
            input_file="f.csv", target_url="http://x.com", framework="playwright"
        )
        result.crawl_success = True
        result.total_failed = 0
        assert result.success is True

    def test_success_false_when_crawl_failed(self) -> None:
        result = OrchestratorResult(
            input_file="f.csv", target_url="http://x.com", framework="playwright"
        )
        result.crawl_success = False
        assert result.success is False

    def test_success_false_when_scripts_failed(self) -> None:
        result = OrchestratorResult(
            input_file="f.csv", target_url="http://x.com", framework="playwright"
        )
        result.total_failed = 1
        assert result.success is False

    def test_output_files_only_successful(self) -> None:
        result = OrchestratorResult(
            input_file="f.csv", target_url="http://x.com", framework="playwright"
        )
        result.scripts = [
            GeneratedScript("TC001", "playwright", "test_tc001.py", "", success=True),
            GeneratedScript("TC002", "playwright", "test_tc002.py", "", success=False),
        ]
        assert result.output_files == ["test_tc001.py"]


# ------------------------------------------------------------------ #
# Happy path                                                           #
# ------------------------------------------------------------------ #


class TestOrchestratorHappyPath:
    def test_run_returns_orchestrator_result(self, tmp_path: Path) -> None:
        orch, _ = _make_orchestrator(tmp_path)
        result = orch.run(
            input_file="inputs/tests.csv",
            target_url="https://www.saucedemo.com",
        )
        assert isinstance(result, OrchestratorResult)

    def test_run_reports_correct_totals(self, tmp_path: Path) -> None:
        orch, _ = _make_orchestrator(tmp_path)
        result = orch.run(
            input_file="inputs/tests.csv",
            target_url="https://www.saucedemo.com",
        )
        assert result.total_test_cases == 2
        assert result.total_generated == 2
        assert result.total_failed == 0

    def test_run_success_true(self, tmp_path: Path) -> None:
        orch, _ = _make_orchestrator(tmp_path)
        result = orch.run(
            input_file="inputs/tests.csv",
            target_url="https://www.saucedemo.com",
        )
        assert result.success is True

    def test_run_output_files_populated(self, tmp_path: Path) -> None:
        orch, _ = _make_orchestrator(tmp_path)
        result = orch.run(
            input_file="inputs/tests.csv",
            target_url="https://www.saucedemo.com",
        )
        assert len(result.output_files) == 2

    def test_run_elapsed_set(self, tmp_path: Path) -> None:
        orch, _ = _make_orchestrator(tmp_path)
        result = orch.run(
            input_file="inputs/tests.csv",
            target_url="https://www.saucedemo.com",
        )
        assert result.elapsed_seconds > 0

    def test_all_four_agents_called(self, tmp_path: Path) -> None:
        orch, mocks = _make_orchestrator(tmp_path)
        orch.run(input_file="inputs/tests.csv", target_url="https://www.saucedemo.com")
        mocks["reader"].run.assert_called_once()
        mocks["auth"].authenticate_from_settings.assert_called_once()
        mocks["crawler"].run.assert_called_once()
        mocks["generator"].generate_batch.assert_called_once()


# ------------------------------------------------------------------ #
# Step 1 — Reader                                                      #
# ------------------------------------------------------------------ #


class TestStepRead:
    def test_empty_test_cases_returns_early(self, tmp_path: Path) -> None:
        orch, mocks = _make_orchestrator(tmp_path, read_test_cases=[])
        result = orch.run(
            input_file="inputs/tests.csv",
            target_url="https://www.saucedemo.com",
        )
        mocks["crawler"].run.assert_not_called()
        mocks["generator"].generate_batch.assert_not_called()
        assert result.total_generated == 0

    def test_reader_exception_adds_warning(self, tmp_path: Path) -> None:
        orch, mocks = _make_orchestrator(tmp_path)
        mocks["reader"].run.side_effect = RuntimeError("file not found")
        result = orch.run(
            input_file="inputs/missing.csv",
            target_url="https://www.saucedemo.com",
        )
        assert any("Reader failed" in w for w in result.warnings)

    def test_reader_exception_returns_early(self, tmp_path: Path) -> None:
        orch, mocks = _make_orchestrator(tmp_path)
        mocks["reader"].run.side_effect = RuntimeError("file not found")
        orch.run(input_file="inputs/missing.csv", target_url="https://www.saucedemo.com")
        mocks["crawler"].run.assert_not_called()


# ------------------------------------------------------------------ #
# Step 2 — Auth                                                        #
# ------------------------------------------------------------------ #


class TestStepAuth:
    def test_auth_skipped_when_use_auth_false(self, tmp_path: Path) -> None:
        orch, mocks = _make_orchestrator(tmp_path, use_auth=False)
        orch.run(input_file="inputs/tests.csv", target_url="https://www.saucedemo.com")
        mocks["auth"].authenticate_from_settings.assert_not_called()

    def test_auth_skipped_when_no_credentials(self, tmp_path: Path) -> None:
        orch, mocks = _make_orchestrator(tmp_path)
        orch._settings.APP_USERNAME = ""
        orch._settings.APP_PASSWORD = ""
        orch.run(input_file="inputs/tests.csv", target_url="https://www.saucedemo.com")
        mocks["auth"].authenticate_from_settings.assert_not_called()

    def test_auth_failure_adds_warning_but_continues(self, tmp_path: Path) -> None:
        from test_forge.agents.auth_agent import AuthError

        orch, mocks = _make_orchestrator(tmp_path)
        mocks["auth"].authenticate_from_settings.side_effect = AuthError("locked out")
        result = orch.run(
            input_file="inputs/tests.csv",
            target_url="https://www.saucedemo.com",
        )
        assert any("Auth failed" in w for w in result.warnings)
        mocks["crawler"].run.assert_called_once()

    def test_session_path_stored_in_result(self, tmp_path: Path) -> None:
        orch, _ = _make_orchestrator(tmp_path, auth_session="/outputs/sessions/s.json")
        result = orch.run(
            input_file="inputs/tests.csv",
            target_url="https://www.saucedemo.com",
        )
        assert result.session_path == "/outputs/sessions/s.json"

    def test_force_auth_refresh_passed_through(self, tmp_path: Path) -> None:
        orch, mocks = _make_orchestrator(tmp_path)
        orch.run(
            input_file="inputs/tests.csv",
            target_url="https://www.saucedemo.com",
            force_auth_refresh=True,
        )
        mocks["auth"].authenticate_from_settings.assert_called_once_with(force_refresh=True)


# ------------------------------------------------------------------ #
# Step 3 — Crawler                                                     #
# ------------------------------------------------------------------ #


class TestStepCrawl:
    def test_session_path_passed_to_crawler(self, tmp_path: Path) -> None:
        orch, mocks = _make_orchestrator(tmp_path, auth_session="/fake/session.json")
        orch.run(input_file="inputs/tests.csv", target_url="https://www.saucedemo.com")
        mocks["crawler"].run.assert_called_once_with(
            "https://www.saucedemo.com",
            auth_state="/fake/session.json",
        )

    def test_crawl_failure_sets_flag(self, tmp_path: Path) -> None:
        orch, _ = _make_orchestrator(tmp_path, crawl_success=False)
        result = orch.run(
            input_file="inputs/tests.csv",
            target_url="https://www.saucedemo.com",
        )
        assert result.crawl_success is False

    def test_crawl_exception_returns_early(self, tmp_path: Path) -> None:
        orch, mocks = _make_orchestrator(tmp_path)
        mocks["crawler"].run.side_effect = RuntimeError("browser crashed")
        result = orch.run(
            input_file="inputs/tests.csv",
            target_url="https://www.saucedemo.com",
        )
        mocks["generator"].generate_batch.assert_not_called()
        assert any("Crawler failed" in w for w in result.warnings)

    def test_crawler_called_once_for_many_test_cases(self, tmp_path: Path) -> None:
        """Crawler runs ONCE even with many test cases — key efficiency guarantee."""
        many_cases = [
            TestCase(
                id=f"TC{i:03}",
                description=f"Test {i}",
                steps=["step"],
                expected_result="ok",
            )
            for i in range(10)
        ]
        orch, mocks = _make_orchestrator(tmp_path, read_test_cases=many_cases)
        orch.run(input_file="inputs/tests.csv", target_url="https://www.saucedemo.com")
        assert mocks["crawler"].run.call_count == 1


# ------------------------------------------------------------------ #
# Step 4 — Generator                                                   #
# ------------------------------------------------------------------ #


class TestStepGenerate:
    def test_failed_scripts_counted(self, tmp_path: Path) -> None:
        scripts = [
            GeneratedScript("TC001", "playwright", "test_tc001.py", "", success=False),
            GeneratedScript("TC002", "playwright", "test_tc002.py", "", success=True),
        ]
        orch, _ = _make_orchestrator(tmp_path, generate_scripts=scripts)
        result = orch.run(
            input_file="inputs/tests.csv",
            target_url="https://www.saucedemo.com",
        )
        assert result.total_failed == 1
        assert result.total_generated == 1

    def test_script_warnings_added_to_result(self, tmp_path: Path) -> None:
        scripts = [
            GeneratedScript(
                "TC001",
                "playwright",
                "test_tc001.py",
                "",
                success=True,
                warnings=["No elements in CrawlResult"],
            ),
        ]
        orch, _ = _make_orchestrator(tmp_path, generate_scripts=scripts)
        result = orch.run(
            input_file="inputs/tests.csv",
            target_url="https://www.saucedemo.com",
        )
        assert any("No elements" in w for w in result.warnings)

    def test_generate_batch_receives_all_test_cases(self, tmp_path: Path) -> None:
        orch, mocks = _make_orchestrator(tmp_path)
        orch.run(input_file="inputs/tests.csv", target_url="https://www.saucedemo.com")
        kwargs = mocks["generator"].generate_batch.call_args.kwargs
        assert len(kwargs["test_cases"]) == 2
