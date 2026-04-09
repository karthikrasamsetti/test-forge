"""
Unit tests for the Reader Agent.
Tests each parser with sample fixture files.
"""

from pathlib import Path

from test_forge.agents.reader_agent import ReaderAgent
from test_forge.core.models import ReadResult
from test_forge.core.models import TestCase as TC

# Path to fixture files
FIXTURES = Path(__file__).parent.parent / "fixtures" / "sample_inputs"


class TestReaderAgentCSV:
    """Tests for CSV parsing."""

    def test_reads_csv_successfully(self):
        agent = ReaderAgent()
        result = agent.run(str(FIXTURES / "sample_test_cases.csv"))
        assert isinstance(result, ReadResult)
        assert result.total_parsed > 0

    def test_csv_returns_test_cases(self):
        agent = ReaderAgent()
        result = agent.run(str(FIXTURES / "sample_test_cases.csv"))
        assert len(result.test_cases) == 5

    def test_csv_test_case_has_required_fields(self):
        agent = ReaderAgent()
        result = agent.run(str(FIXTURES / "sample_test_cases.csv"))
        tc = result.test_cases[0]
        assert tc.id == "TC001"
        assert "valid credentials" in tc.description.lower()
        assert len(tc.steps) > 0
        assert tc.expected_result != ""

    def test_csv_steps_are_parsed_as_list(self):
        agent = ReaderAgent()
        result = agent.run(str(FIXTURES / "sample_test_cases.csv"))
        tc = result.test_cases[0]
        assert isinstance(tc.steps, list)
        assert all(isinstance(s, str) for s in tc.steps)

    def test_csv_priority_normalized(self):
        agent = ReaderAgent()
        result = agent.run(str(FIXTURES / "sample_test_cases.csv"))
        for tc in result.test_cases:
            assert tc.priority in ("high", "medium", "low")

    def test_csv_url_extracted(self):
        agent = ReaderAgent()
        result = agent.run(str(FIXTURES / "sample_test_cases.csv"))
        tc = result.test_cases[0]
        assert "saucedemo.com" in tc.url


class TestReaderAgentMarkdown:
    """Tests for Markdown parsing."""

    def test_reads_markdown_successfully(self):
        agent = ReaderAgent()
        result = agent.run(str(FIXTURES / "sample_test_cases.md"))
        assert isinstance(result, ReadResult)
        assert result.total_parsed > 0

    def test_markdown_returns_test_cases(self):
        agent = ReaderAgent()
        result = agent.run(str(FIXTURES / "sample_test_cases.md"))
        assert len(result.test_cases) == 3

    def test_markdown_ids_extracted(self):
        agent = ReaderAgent()
        result = agent.run(str(FIXTURES / "sample_test_cases.md"))
        ids = [tc.id for tc in result.test_cases]
        assert "TC001" in ids
        assert "TC002" in ids

    def test_markdown_steps_parsed(self):
        agent = ReaderAgent()
        result = agent.run(str(FIXTURES / "sample_test_cases.md"))
        tc = result.test_cases[0]
        assert len(tc.steps) >= 3

    def test_markdown_url_extracted(self):
        agent = ReaderAgent()
        result = agent.run(str(FIXTURES / "sample_test_cases.md"))
        tc = result.test_cases[0]
        assert "saucedemo.com" in tc.url

    def test_markdown_category_extracted(self):
        agent = ReaderAgent()
        result = agent.run(str(FIXTURES / "sample_test_cases.md"))
        tc = result.test_cases[0]
        assert tc.category == "authentication"


class TestReaderAgentEdgeCases:
    """Tests for edge cases and error handling."""

    def test_file_not_found_returns_empty_result(self):
        agent = ReaderAgent()
        result = agent.run("nonexistent_file.csv")
        assert result.total_parsed == 0
        assert len(result.warnings) > 0
        assert "not found" in result.warnings[0].lower()

    def test_unsupported_format_returns_warning(self):
        agent = ReaderAgent()
        result = agent.run("test.xyz")
        assert result.total_parsed == 0
        assert len(result.warnings) > 0
        assert "unsupported" in result.warnings[0].lower()

    def test_result_has_source_file(self):
        agent = ReaderAgent()
        result = agent.run(str(FIXTURES / "sample_test_cases.csv"))
        assert result.source_file != ""

    def test_result_file_format_detected(self):
        agent = ReaderAgent()
        result = agent.run(str(FIXTURES / "sample_test_cases.csv"))
        assert result.file_format == "csv"

    def test_read_result_success_rate(self):
        agent = ReaderAgent()
        result = agent.run(str(FIXTURES / "sample_test_cases.csv"))
        assert result.success_rate == 1.0

    def test_read_result_success_rate_empty(self):
        agent = ReaderAgent()
        result = agent.run("nonexistent_file.csv")
        assert result.success_rate == 0.0

    def test_csv_category_extracted(self):
        agent = ReaderAgent()
        result = agent.run(str(FIXTURES / "sample_test_cases.csv"))
        tc = result.test_cases[0]
        assert tc.category == "authentication"

    def test_csv_preconditions_extracted(self):
        agent = ReaderAgent()
        result = agent.run(str(FIXTURES / "sample_test_cases.csv"))
        tc = result.test_cases[0]
        assert "user" in tc.preconditions.lower()


class TestTestCaseModel:
    """Tests for the TestCase Pydantic model."""

    def test_steps_parsed_from_string(self):
        tc = TC(
            id="TC001",
            description="Test login",
            steps="1. Go to login\n2. Enter credentials\n3. Click submit",
            expected_result="User is logged in",
        )
        assert len(tc.steps) == 3
        assert tc.steps[0] == "Go to login"

    def test_priority_normalized_to_lowercase(self):
        tc = TC(
            id="TC001",
            description="Test",
            steps=[],
            expected_result="Pass",
            priority="HIGH",
        )
        assert tc.priority == "high"

    def test_invalid_priority_defaults_to_medium(self):
        tc = TC(
            id="TC001",
            description="Test",
            steps=[],
            expected_result="Pass",
            priority="critical",
        )
        assert tc.priority == "medium"

    def test_id_converted_to_string(self):
        tc = TC(
            id=1,
            description="Test",
            steps=[],
            expected_result="Pass",
        )
        assert tc.id == "1"
        assert isinstance(tc.id, str)
