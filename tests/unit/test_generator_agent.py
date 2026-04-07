"""Unit tests for GeneratorAgent, PlaywrightTemplate, and action parser."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from test_forge.agents.generator_agent import (
    GeneratorAgent,
    _build_element_summary,
    _parse_action_lines,
)
from test_forge.core.models import (
    CrawlResult,
    PageElement,
    PageMetadata,
    TestCase,
)
from test_forge.frameworks.base_template import GeneratedScript
from test_forge.frameworks.playwright_template import PlaywrightTemplate

# ------------------------------------------------------------------ #
# Fixtures                                                             #
# ------------------------------------------------------------------ #


@pytest.fixture
def sample_test_case() -> TestCase:
    return TestCase(
        id="TC001",
        description="Login with valid credentials",
        steps=[
            "Navigate to the login page",
            "Enter 'standard_user' in the username field",
            "Enter 'secret_sauce' in the password field",
            "Click the login button",
            "Verify redirect to inventory page",
        ],
        expected_result="User is redirected to /inventory.html",
        preconditions="User is on the login page",
        priority="high",
        url="https://www.saucedemo.com",
    )


@pytest.fixture
def sample_test_case_negative() -> TestCase:
    return TestCase(
        id="TC002",
        description="Login with locked out user",
        steps=[
            "Navigate to the login page",
            "Enter 'locked_out_user' in the username field",
            "Enter 'secret_sauce' in the password field",
            "Click the login button",
            "Verify error message is shown",
        ],
        expected_result="Error message 'Epic sadface' is displayed",
        priority="high",
    )


@pytest.fixture
def sample_elements() -> list[PageElement]:
    return [
        PageElement(
            tag="input",
            selector="[data-test='username']",
            type="text",
            placeholder="Username",
            label="Username",
            is_visible=True,
            is_enabled=True,
            triggers_navigation=False,
            attributes={"data-test": "username"},
            selector_strategy="data-test",
        ),
        PageElement(
            tag="input",
            selector="[data-test='password']",
            type="password",
            placeholder="Password",
            label="Password",
            is_visible=True,
            is_enabled=True,
            triggers_navigation=False,
            attributes={"data-test": "password"},
            selector_strategy="data-test",
        ),
        PageElement(
            tag="input",
            selector="[data-test='login-button']",
            type="submit",
            text="Login",
            label="Login",
            is_visible=True,
            is_enabled=True,
            triggers_navigation=True,
            attributes={"data-test": "login-button"},
            selector_strategy="data-test",
        ),
        PageElement(
            tag="h3",
            selector="[data-test='error']",
            type="",
            text="Epic sadface",
            is_visible=True,
            is_enabled=True,
            triggers_navigation=False,
            attributes={"data-test": "error"},
            selector_strategy="data-test",
        ),
    ]


@pytest.fixture
def sample_crawl_result(sample_elements: list[PageElement]) -> CrawlResult:
    return CrawlResult(
        url="https://www.saucedemo.com",
        metadata=PageMetadata(
            url="https://www.saucedemo.com",
            title="Swag Labs",
            is_spa=True,
            framework="React",
        ),
        elements=sample_elements,
        forms=[],
        warnings=[],
        success=True,
    )


@pytest.fixture
def template() -> PlaywrightTemplate:
    return PlaywrightTemplate()


# ------------------------------------------------------------------ #
# PlaywrightTemplate — action builders                                 #
# ------------------------------------------------------------------ #


class TestPlaywrightTemplateActions:
    def test_action_goto(self, template: PlaywrightTemplate) -> None:
        assert template.action_goto("https://example.com") == 'page.goto("https://example.com")'

    def test_action_fill(self, template: PlaywrightTemplate) -> None:
        result = template.action_fill("[data-test='username']", "standard_user")
        assert result == 'page.fill("[data-test=\'username\']", "standard_user")'

    def test_action_click(self, template: PlaywrightTemplate) -> None:
        assert template.action_click("[data-test='login-button']") == (
            "page.click(\"[data-test='login-button']\")"
        )

    def test_action_wait_navigation(self, template: PlaywrightTemplate) -> None:
        result = template.action_wait_navigation("/inventory.html")
        assert result == 'page.wait_for_url("**/inventory.html")'

    def test_action_assert_url(self, template: PlaywrightTemplate) -> None:
        result = template.action_assert_url("/inventory.html")
        assert "expect(page)" in result
        assert "to_have_url" in result

    def test_action_assert_visible(self, template: PlaywrightTemplate) -> None:
        result = template.action_assert_visible("[data-test='error']")
        assert "to_be_visible" in result

    def test_action_assert_text(self, template: PlaywrightTemplate) -> None:
        result = template.action_assert_text("[data-test='error']", "Epic sadface")
        assert "to_contain_text" in result
        assert "Epic sadface" in result

    def test_action_assert_not_visible(self, template: PlaywrightTemplate) -> None:
        result = template.action_assert_not_visible("[data-test='error']")
        assert "not_to_be_visible" in result

    def test_action_select(self, template: PlaywrightTemplate) -> None:
        result = template.action_select("#dropdown", "option1")
        assert 'page.select_option("#dropdown", "option1")' == result


# ------------------------------------------------------------------ #
# PlaywrightTemplate — render                                          #
# ------------------------------------------------------------------ #


class TestPlaywrightTemplateRender:
    def test_render_contains_function_name(self, template: PlaywrightTemplate) -> None:
        content = template.render(
            test_id="TC001",
            description="Login with valid credentials",
            steps_code=['page.goto("https://example.com")'],
            base_url="https://example.com",
        )
        assert "def test_tc001_login_with_valid_credentials" in content

    def test_render_contains_docstring(self, template: PlaywrightTemplate) -> None:
        content = template.render(
            test_id="TC001",
            description="Login with valid credentials",
            steps_code=[],
            base_url="https://example.com",
        )
        assert "TC001" in content
        assert "Login with valid credentials" in content

    def test_render_contains_imports(self, template: PlaywrightTemplate) -> None:
        content = template.render(
            test_id="TC001",
            description="Test",
            steps_code=[],
            base_url="https://example.com",
        )
        assert "from playwright.sync_api import Page, expect" in content
        assert "import re" in content

    def test_render_steps_indented(self, template: PlaywrightTemplate) -> None:
        steps = ['page.goto("https://example.com")', "page.click(\"[data-test='btn']\")"]
        content = template.render(
            test_id="TC001",
            description="Test",
            steps_code=steps,
            base_url="https://example.com",
        )
        # Steps must be indented inside the function
        assert '    page.goto("https://example.com")' in content

    def test_render_conftest_contains_base_url(self, template: PlaywrightTemplate) -> None:
        conftest = template.render_conftest(base_url="https://www.saucedemo.com")
        assert "https://www.saucedemo.com" in conftest
        assert "base_url" in conftest

    def test_render_conftest_has_logged_in_fixture(self, template: PlaywrightTemplate) -> None:
        conftest = template.render_conftest(base_url="https://www.saucedemo.com")
        assert "logged_in_page" in conftest


# ------------------------------------------------------------------ #
# BaseTemplate — shared helpers                                        #
# ------------------------------------------------------------------ #


class TestBaseTemplateHelpers:
    def test_slugify_basic(self, template: PlaywrightTemplate) -> None:
        assert template.slugify("Login with valid credentials") == "login_with_valid_credentials"

    def test_slugify_special_chars(self, template: PlaywrightTemplate) -> None:
        slug = template.slugify("Test: Add item (to cart)!")
        assert re.match(r"^[a-z0-9_]+$", slug)

    def test_slugify_truncated(self, template: PlaywrightTemplate) -> None:
        long_text = "a" * 100
        assert len(template.slugify(long_text)) <= 60

    def test_build_filename(self, template: PlaywrightTemplate) -> None:
        name = template.build_filename("TC001", "Login with valid credentials")
        assert name == "test_tc001_login_with_valid_credentials.py"

    def test_build_filename_starts_with_test(self, template: PlaywrightTemplate) -> None:
        name = template.build_filename("TC005", "Complete checkout flow")
        assert name.startswith("test_")
        assert name.endswith(".py")


# ------------------------------------------------------------------ #
# Action line parser                                                   #
# ------------------------------------------------------------------ #


class TestActionParser:
    def setup_method(self) -> None:
        self.template = PlaywrightTemplate()
        self.warnings: list[str] = []

    def test_parses_goto(self) -> None:
        lines = _parse_action_lines("goto: https://www.saucedemo.com", self.template, self.warnings)
        assert lines == ['page.goto("https://www.saucedemo.com")']

    def test_parses_fill(self) -> None:
        lines = _parse_action_lines(
            "fill: [data-test='username'] | standard_user", self.template, self.warnings
        )
        assert len(lines) == 1
        assert "page.fill" in lines[0]
        assert "standard_user" in lines[0]

    def test_parses_click(self) -> None:
        lines = _parse_action_lines(
            "click: [data-test='login-button']", self.template, self.warnings
        )
        assert "page.click" in lines[0]

    def test_parses_wait_navigation(self) -> None:
        lines = _parse_action_lines(
            "wait_navigation: /inventory.html", self.template, self.warnings
        )
        assert "wait_for_url" in lines[0]

    def test_parses_comment(self) -> None:
        lines = _parse_action_lines(
            "comment: Verify user is on inventory page", self.template, self.warnings
        )
        assert lines[0].startswith("# Verify")

    def test_skips_blank_lines(self) -> None:
        raw = "\ngoto: https://example.com\n\nclick: #btn\n"
        lines = _parse_action_lines(raw, self.template, self.warnings)
        assert len(lines) == 2

    def test_unknown_action_becomes_comment(self) -> None:
        lines = _parse_action_lines("hover: #element", self.template, self.warnings)
        assert "UNKNOWN ACTION" in lines[0]
        assert len(self.warnings) == 1

    def test_unparseable_line_becomes_comment(self) -> None:
        lines = _parse_action_lines("this is not valid", self.template, self.warnings)
        assert "UNPARSED" in lines[0]
        assert len(self.warnings) == 1

    def test_parses_multiple_actions(self) -> None:
        raw = """
goto: https://www.saucedemo.com
fill: [data-test='username'] | standard_user
fill: [data-test='password'] | secret_sauce
click: [data-test='login-button']
wait_navigation: /inventory.html
assert_url: /inventory.html
"""
        lines = _parse_action_lines(raw, self.template, self.warnings)
        assert len(lines) == 6
        assert not self.warnings


# ------------------------------------------------------------------ #
# Element summary builder                                              #
# ------------------------------------------------------------------ #


class TestElementSummary:
    def test_summary_includes_selector(self, sample_elements: list[PageElement]) -> None:
        summary = _build_element_summary(sample_elements)
        assert "[data-test='username']" in summary

    def test_summary_includes_navigation_flag(self, sample_elements: list[PageElement]) -> None:
        summary = _build_element_summary(sample_elements)
        assert "triggers_navigation=True" in summary

    def test_summary_empty_list(self) -> None:
        assert _build_element_summary([]) == ""


# ------------------------------------------------------------------ #
# GeneratorAgent — unit tests with mocked LLM                         #
# ------------------------------------------------------------------ #

MOCK_LLM_RESPONSE = """\
goto: https://www.saucedemo.com
fill: [data-test='username'] | standard_user
fill: [data-test='password'] | secret_sauce
click: [data-test='login-button']
wait_navigation: /inventory.html
assert_url: /inventory.html
"""


def _make_agent(tmp_path: Path) -> GeneratorAgent:
    """Create a GeneratorAgent with mocked LLM and temp output dir."""
    with patch("test_forge.agents.generator_agent.get_llm") as mock_get_llm:
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_LLM_RESPONSE
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        agent = GeneratorAgent(
            framework="playwright",
            output_dir=str(tmp_path),
            save_to_disk=False,
        )
        # Attach mock so tests can assert call counts
        agent.llm = mock_llm
        return agent


class TestGeneratorAgent:
    def test_generate_returns_generated_script(
        self,
        tmp_path: Path,
        sample_test_case: TestCase,
        sample_crawl_result: CrawlResult,
    ) -> None:
        agent = _make_agent(tmp_path)
        script = agent.generate(test_case=sample_test_case, crawl_result=sample_crawl_result)
        assert isinstance(script, GeneratedScript)
        assert script.success is True

    def test_generate_correct_filename(
        self,
        tmp_path: Path,
        sample_test_case: TestCase,
        sample_crawl_result: CrawlResult,
    ) -> None:
        agent = _make_agent(tmp_path)
        script = agent.generate(test_case=sample_test_case, crawl_result=sample_crawl_result)
        assert script.filename == "test_tc001_login_with_valid_credentials.py"

    def test_generate_content_has_goto(
        self,
        tmp_path: Path,
        sample_test_case: TestCase,
        sample_crawl_result: CrawlResult,
    ) -> None:
        agent = _make_agent(tmp_path)
        script = agent.generate(test_case=sample_test_case, crawl_result=sample_crawl_result)
        assert "page.goto" in script.content

    def test_generate_content_has_fill(
        self,
        tmp_path: Path,
        sample_test_case: TestCase,
        sample_crawl_result: CrawlResult,
    ) -> None:
        agent = _make_agent(tmp_path)
        script = agent.generate(test_case=sample_test_case, crawl_result=sample_crawl_result)
        assert "page.fill" in script.content

    def test_generate_content_has_function_def(
        self,
        tmp_path: Path,
        sample_test_case: TestCase,
        sample_crawl_result: CrawlResult,
    ) -> None:
        agent = _make_agent(tmp_path)
        script = agent.generate(test_case=sample_test_case, crawl_result=sample_crawl_result)
        assert "def test_tc001" in script.content

    def test_generate_has_conftest(
        self,
        tmp_path: Path,
        sample_test_case: TestCase,
        sample_crawl_result: CrawlResult,
    ) -> None:
        agent = _make_agent(tmp_path)
        script = agent.generate(test_case=sample_test_case, crawl_result=sample_crawl_result)
        assert "base_url" in script.conftest_content

    def test_generate_no_warnings_on_clean_input(
        self,
        tmp_path: Path,
        sample_test_case: TestCase,
        sample_crawl_result: CrawlResult,
    ) -> None:
        agent = _make_agent(tmp_path)
        script = agent.generate(test_case=sample_test_case, crawl_result=sample_crawl_result)
        assert script.warnings == []

    def test_generate_warns_on_failed_crawl(
        self,
        tmp_path: Path,
        sample_test_case: TestCase,
        sample_crawl_result: CrawlResult,
    ) -> None:
        sample_crawl_result.success = False
        agent = _make_agent(tmp_path)
        script = agent.generate(test_case=sample_test_case, crawl_result=sample_crawl_result)
        assert any("CrawlResult.success=False" in w for w in script.warnings)

    def test_generate_warns_on_empty_elements(
        self,
        tmp_path: Path,
        sample_test_case: TestCase,
        sample_crawl_result: CrawlResult,
    ) -> None:
        sample_crawl_result.elements = []
        agent = _make_agent(tmp_path)
        script = agent.generate(test_case=sample_test_case, crawl_result=sample_crawl_result)
        assert any("No elements" in w for w in script.warnings)

    def test_generate_batch_returns_all(
        self,
        tmp_path: Path,
        sample_test_case: TestCase,
        sample_test_case_negative: TestCase,
        sample_crawl_result: CrawlResult,
    ) -> None:
        agent = _make_agent(tmp_path)
        scripts = agent.generate_batch(
            test_cases=[sample_test_case, sample_test_case_negative],
            crawl_result=sample_crawl_result,
        )
        assert len(scripts) == 2

    def test_generate_saves_to_disk(
        self,
        tmp_path: Path,
        sample_test_case: TestCase,
        sample_crawl_result: CrawlResult,
    ) -> None:
        with patch("test_forge.agents.generator_agent.get_llm") as mock_get_llm:
            mock_llm = MagicMock()
            mock_response = MagicMock()
            mock_response.content = MOCK_LLM_RESPONSE
            mock_llm.invoke.return_value = mock_response
            mock_get_llm.return_value = mock_llm

            agent = GeneratorAgent(
                framework="playwright",
                output_dir=str(tmp_path),
                save_to_disk=True,
            )
            agent.llm = mock_llm
            script = agent.generate(test_case=sample_test_case, crawl_result=sample_crawl_result)

        saved = tmp_path / "playwright" / script.filename
        assert saved.exists()
        assert saved.read_text(encoding="utf-8") == script.content

    def test_invalid_framework_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Unsupported framework"):
            GeneratorAgent(framework="selenium", output_dir=str(tmp_path))

    def test_llm_failure_produces_warning(
        self,
        tmp_path: Path,
        sample_test_case: TestCase,
        sample_crawl_result: CrawlResult,
    ) -> None:
        with patch("test_forge.agents.generator_agent.get_llm") as mock_get_llm:
            mock_llm = MagicMock()
            mock_llm.invoke.side_effect = RuntimeError("API down")
            mock_get_llm.return_value = mock_llm

            agent = GeneratorAgent(
                framework="playwright",
                output_dir=str(tmp_path),
                save_to_disk=False,
            )
            agent.llm = mock_llm
            script = agent.generate(test_case=sample_test_case, crawl_result=sample_crawl_result)

        assert any("LLM call failed" in w for w in script.warnings)
        assert "pass" in script.content  # fallback placeholder


import re  # noqa: E402 — needed for TestBaseTemplateHelpers
