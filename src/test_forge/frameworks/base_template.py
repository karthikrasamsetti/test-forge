"""Base template interface for all test framework code generators."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class GeneratedScript:
    """Holds a fully generated test script and its metadata."""

    test_id: str
    framework: str
    filename: str
    content: str
    conftest_content: str = ""
    warnings: list[str] = field(default_factory=list)
    success: bool = True


class BaseTemplate(ABC):
    """Abstract base for all test-framework code templates.

    Subclasses encode framework-specific best practices: import style,
    fixture signatures, assertion APIs, and wait strategies.
    The generator agent calls ``render`` with LLM-produced action lines
    and receives a complete, runnable test file back.
    """

    framework_name: str = ""

    # ------------------------------------------------------------------ #
    # Abstract interface — subclasses must implement all of these          #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def render(
        self,
        *,
        test_id: str,
        description: str,
        steps_code: list[str],
        base_url: str,
        imports_extra: list[str] | None = None,
        use_auth_fixture: bool = False,
    ) -> str:
        """Return the full content of a runnable test file."""

    @abstractmethod
    def render_conftest(self, *, base_url: str) -> str:
        """Return the content of the shared conftest.py for this suite."""

    @abstractmethod
    def action_goto(self, url: str) -> str:
        """Navigate to a URL."""

    @abstractmethod
    def action_fill(self, selector: str, value: str) -> str:
        """Fill a text input."""

    @abstractmethod
    def action_click(self, selector: str) -> str:
        """Click an element."""

    @abstractmethod
    def action_select(self, selector: str, value: str) -> str:
        """Select a dropdown option."""

    @abstractmethod
    def action_wait_navigation(self, url_pattern: str) -> str:
        """Wait for URL change after a navigation-triggering click."""

    @abstractmethod
    def action_assert_url(self, url_pattern: str) -> str:
        """Assert the current URL matches a pattern."""

    @abstractmethod
    def action_assert_visible(self, selector: str) -> str:
        """Assert an element is visible on the page."""

    @abstractmethod
    def action_assert_text(self, selector: str, text: str) -> str:
        """Assert an element contains specific text."""

    @abstractmethod
    def action_assert_not_visible(self, selector: str) -> str:
        """Assert an element is NOT visible (negative tests)."""

    # ------------------------------------------------------------------ #
    # Shared helpers — available to all subclasses                         #
    # ------------------------------------------------------------------ #

    @staticmethod
    def slugify(text: str) -> str:
        """Convert a description to a safe Python identifier fragment.

        Example: "Login with valid credentials" -> "login_with_valid_credentials"
        """
        slug = text.lower()
        slug = re.sub(r"[^a-z0-9]+", "_", slug)
        slug = slug.strip("_")
        return slug[:60]

    @staticmethod
    def indent(code: str, spaces: int = 4) -> str:
        """Indent every line of a code block by N spaces."""
        pad = " " * spaces
        return "\n".join(pad + line if line.strip() else line for line in code.splitlines())

    def build_filename(self, test_id: str, description: str) -> str:
        """Build the output filename from test ID and description.

        Example: "TC001", "Login with valid credentials"
                 -> "test_tc001_login_with_valid_credentials.py"
        """
        slug = self.slugify(description)
        safe_id = test_id.lower().replace(" ", "_")
        return f"test_{safe_id}_{slug}.py"
