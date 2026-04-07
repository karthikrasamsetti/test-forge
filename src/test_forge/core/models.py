"""
Core data models for test-forge.
All agents share these models — single source of truth.
"""

from pydantic import BaseModel, Field, field_validator


class TestCase(BaseModel):
    """
    Represents a single test case extracted from any input format.
    All reader agents produce this model regardless of source format.
    """

    id: str = Field(..., description="Unique test case identifier e.g. TC001")
    description: str = Field(..., description="What this test case verifies")
    steps: list[str] = Field(default_factory=list, description="Ordered list of steps")
    expected_result: str = Field(..., description="What should happen if test passes")
    preconditions: str = Field(default="", description="What must be true before test runs")
    priority: str = Field(default="medium", description="high | medium | low")
    category: str = Field(default="general", description="e.g. authentication, checkout")
    url: str = Field(default="", description="URL under test")
    raw_text: str = Field(default="", description="Original raw text for debugging")

    @field_validator("priority")
    @classmethod
    def normalize_priority(cls, v: str) -> str:
        """Normalize priority to lowercase."""
        normalized = v.lower().strip()
        if normalized not in ("high", "medium", "low"):
            return "medium"
        return normalized

    @field_validator("steps", mode="before")
    @classmethod
    def parse_steps(cls, v: object) -> list[str]:
        """
        Handle steps that come in as a single string with newlines or numbers.
        '1. Click login\\n2. Enter password' → ['Click login', 'Enter password']
        """
        if isinstance(v, list):
            return [str(s).strip() for s in v if str(s).strip()]

        if isinstance(v, str):
            lines = v.replace("\r\n", "\n").split("\n")
            steps = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                # Remove leading numbers like "1." "1)" "Step 1:"
                import re

                line = re.sub(
                    r"^(\d+[\.\):]?\s*|step\s*\d+[:\.]?\s*)", "", line, flags=re.IGNORECASE
                )
                if line:
                    steps.append(line)
            return steps

        return []

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, v: object) -> str:
        """Ensure ID is always a string."""
        return str(v).strip()

    class Config:
        str_strip_whitespace = True


class ReadResult(BaseModel):
    """Result returned by the Reader Agent."""

    source_file: str
    file_format: str  # excel | csv | word | pdf | markdown | text
    test_cases: list[TestCase]
    total_found: int
    total_parsed: int
    skipped: int
    warnings: list[str] = Field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.total_found == 0:
            return 0.0
        return self.total_parsed / self.total_found


# ── Crawler models ─────────────────────────────────────────────────────────────


class PageElement(BaseModel):
    """A single interactive element found on a web page."""

    tag: str = Field(..., description="HTML tag: input, button, a, select, textarea")
    selector: str = Field(..., description="Best CSS selector for this element")
    type: str = Field(default="", description="Input type: text, password, submit, etc.")
    name: str = Field(default="", description="name attribute")
    placeholder: str = Field(default="", description="placeholder attribute")
    label: str = Field(default="", description="Associated label text")
    text: str = Field(default="", description="Visible text content")
    role: str = Field(default="", description="ARIA role")
    is_visible: bool = Field(default=True)
    is_enabled: bool = Field(default=True)
    triggers_navigation: bool = Field(
        default=False, description="True if clicking this element causes page navigation"
    )
    attributes: dict[str, str] = Field(
        default_factory=dict, description="All data-* and aria-* attributes"
    )
    selector_strategy: str = Field(
        default="css",
        description="How the selector was chosen: data-test|id|aria|name|placeholder|css",
    )


class FormInfo(BaseModel):
    """A form found on the page with its fields."""

    selector: str
    action: str = ""
    method: str = "get"
    fields: list[PageElement] = Field(default_factory=list)
    submit: PageElement | None = None


class PageMetadata(BaseModel):
    """Page-level information detected by the crawler."""

    url: str
    title: str = ""
    is_spa: bool = False
    framework: str = ""  # react | vue | angular | unknown
    load_time_ms: int = 0
    has_auth: bool = False  # True if login form detected


class CrawlResult(BaseModel):
    """Complete result returned by the Crawler Agent."""

    url: str
    metadata: PageMetadata
    elements: list[PageElement] = Field(default_factory=list)
    forms: list[FormInfo] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    success: bool = True

    @property
    def interactive_elements(self) -> list[PageElement]:
        """Returns only visible and enabled elements."""
        return [e for e in self.elements if e.is_visible and e.is_enabled]

    @property
    def inputs(self) -> list[PageElement]:
        """Returns all input fields."""
        return [e for e in self.elements if e.tag == "input"]

    @property
    def buttons(self) -> list[PageElement]:
        """Returns all buttons and submit inputs."""
        return [
            e
            for e in self.elements
            if e.tag == "button" or (e.tag == "input" and e.type == "submit")
        ]
