"""Generator Agent — translates TestCase + CrawlResult into runnable test scripts."""

from __future__ import annotations

import re
from pathlib import Path

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from test_forge.core.llm import get_llm
from test_forge.core.models import CrawlResult, PageElement, TestCase
from test_forge.frameworks.base_template import BaseTemplate, GeneratedScript
from test_forge.frameworks.playwright_template import PlaywrightTemplate

logger = structlog.get_logger(__name__)

# ------------------------------------------------------------------ #
# Supported frameworks registry                                        #
# ------------------------------------------------------------------ #

FRAMEWORK_REGISTRY: dict[str, BaseTemplate] = {
    "playwright": PlaywrightTemplate(),
}


# ------------------------------------------------------------------ #
# LLM prompt helpers                                                   #
# ------------------------------------------------------------------ #

SYSTEM_PROMPT = """\
You are a test automation expert. Your job is to convert manual test case steps
into structured test actions using ONLY the selectors provided.

Rules:
- Use ONLY selectors from the provided element list — never invent selectors.
- Each action must be on its own line in the format: ACTION: arguments
- Supported actions and their formats:
    goto: <url>
    fill: <selector> | <value>
    click: <selector>
    select: <selector> | <value>
    wait_navigation: <url_path>
    assert_url: <url_path_regex>
    assert_visible: <selector>
    assert_text: <selector> | <text>
    assert_not_visible: <selector>
    comment: <human readable note>

- For navigation steps (login buttons, submit buttons), always add a
  wait_navigation line AFTER the click if the page URL will change.
- For negative tests (locked user, wrong password), add assert_visible for
  the error message selector and assert_url for the login page staying put.
- Do NOT add imports, function signatures, or any Python code — only actions.
- Respond with ONLY the action lines, nothing else.
"""


def _build_element_summary(elements: list[PageElement]) -> str:
    """Format crawled elements as a compact list for the LLM prompt."""
    lines: list[str] = []
    for el in elements:
        parts = [
            f"tag={el.tag}",
            f"selector={el.selector}",
        ]
        if el.label:
            parts.append(f"label={el.label!r}")
        if el.placeholder:
            parts.append(f"placeholder={el.placeholder!r}")
        if el.type:
            parts.append(f"type={el.type}")
        if el.text:
            parts.append(f"text={el.text!r}")
        if el.triggers_navigation:
            parts.append("triggers_navigation=True")
        lines.append("  - " + ", ".join(parts))
    return "\n".join(lines)


def _build_user_prompt(test_case: TestCase, crawl_result: CrawlResult) -> str:
    """Build the full user prompt sent to the LLM."""
    steps_block = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(test_case.steps))
    element_block = _build_element_summary(crawl_result.elements)

    return f"""\
## Test Case
ID: {test_case.id}
Description: {test_case.description}
Priority: {test_case.priority}
Preconditions: {test_case.preconditions or "None"}
Expected Result: {test_case.expected_result}

## Steps
{steps_block}

## Available Page Elements (from live crawl of {crawl_result.url})
{element_block}

Generate the action lines for this test case now.
"""


# ------------------------------------------------------------------ #
# Action line parser                                                   #
# ------------------------------------------------------------------ #

# Maps action keyword -> template method name
_ACTION_MAP = {
    "goto": "action_goto",
    "fill": "action_fill",
    "click": "action_click",
    "select": "action_select",
    "wait_navigation": "action_wait_navigation",
    "assert_url": "action_assert_url",
    "assert_visible": "action_assert_visible",
    "assert_text": "action_assert_text",
    "assert_not_visible": "action_assert_not_visible",
}


def _parse_action_lines(
    raw: str,
    template: BaseTemplate,
    warnings: list[str],
) -> list[str]:
    """Convert raw LLM action lines into framework-specific code lines.

    Each line format:  ACTION: arg1 | arg2
    Unknown actions become comments so the file is still runnable.
    """
    code_lines: list[str] = []

    for raw_line in raw.strip().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # comment passthrough
        if line.lower().startswith("comment:"):
            text = line.split(":", 1)[1].strip()
            code_lines.append(f"# {text}")
            continue

        # parse  ACTION: args
        match = re.match(r"^(\w+):\s*(.+)$", line)
        if not match:
            warnings.append(f"Could not parse action line: {line!r}")
            code_lines.append(f"# UNPARSED: {line}")
            continue

        action_key = match.group(1).lower()
        args_raw = [a.strip() for a in match.group(2).split("|")]

        method_name = _ACTION_MAP.get(action_key)
        if method_name is None:
            warnings.append(f"Unknown action '{action_key}' — kept as comment")
            code_lines.append(f"# UNKNOWN ACTION: {line}")
            continue

        method = getattr(template, method_name)
        try:
            code_line = method(*args_raw)
            code_lines.append(code_line)
        except TypeError as exc:
            warnings.append(f"Wrong args for '{action_key}': {exc}")
            code_lines.append(f"# ARG ERROR: {line}")

    return code_lines


# ------------------------------------------------------------------ #
# Generator Agent                                                      #
# ------------------------------------------------------------------ #


class GeneratorAgent:
    """Generates test scripts from a TestCase and a CrawlResult.

    Usage::

        agent = GeneratorAgent(framework="playwright", output_dir="./outputs")
        script = agent.generate(test_case=tc, crawl_result=cr)
        print(script.filename)   # test_tc001_login_with_valid_credentials.py
        print(script.content)    # full Python source
    """

    def __init__(
        self,
        framework: str = "playwright",
        output_dir: str = "./outputs",
        save_to_disk: bool = True,
    ) -> None:
        if framework not in FRAMEWORK_REGISTRY:
            raise ValueError(
                f"Unsupported framework '{framework}'. Choose from: {list(FRAMEWORK_REGISTRY)}"
            )
        self.framework = framework
        self.template: BaseTemplate = FRAMEWORK_REGISTRY[framework]
        self.output_dir = Path(output_dir) / framework
        self.save_to_disk = save_to_disk
        self.llm = get_llm()
        self._log = logger.bind(agent="generator", framework=framework)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def generate(self, *, test_case: TestCase, crawl_result: CrawlResult) -> GeneratedScript:
        """Generate a test script for one TestCase.

        Steps:
        1. Build LLM prompt with test steps + real selectors
        2. Call LLM → get action lines
        3. Parse action lines → framework code lines
        4. Render full file via template
        5. Optionally save to disk
        """
        self._log.info("generating", test_id=test_case.id, url=crawl_result.url)
        warnings: list[str] = []

        # 1. Warn if crawl had issues
        if not crawl_result.success:
            warnings.append(f"CrawlResult.success=False for {crawl_result.url}")
        if not crawl_result.elements:
            warnings.append("No elements in CrawlResult — selectors may be invented")

        # 2. Call LLM
        raw_actions = self._call_llm(test_case, crawl_result, warnings)

        # 3. Parse to code lines
        code_lines = _parse_action_lines(raw_actions, self.template, warnings)

        if not code_lines:
            warnings.append("LLM returned no parseable actions — empty test body")
            code_lines = ["pass  # generator produced no actions"]

        # 4. Render full file
        filename = self.template.build_filename(test_case.id, test_case.description)
        content = self.template.render(
            test_id=test_case.id,
            description=test_case.description,
            steps_code=code_lines,
            base_url=crawl_result.url,
        )
        conftest = self.template.render_conftest(base_url=crawl_result.url)

        script = GeneratedScript(
            test_id=test_case.id,
            framework=self.framework,
            filename=filename,
            content=content,
            conftest_content=conftest,
            warnings=warnings,
            success=True,
        )

        # 5. Save to disk
        if self.save_to_disk:
            self._save(script)

        self._log.info(
            "generated",
            test_id=test_case.id,
            filename=filename,
            warnings=len(warnings),
        )
        return script

    def generate_batch(
        self,
        *,
        test_cases: list[TestCase],
        crawl_result: CrawlResult,
    ) -> list[GeneratedScript]:
        """Generate scripts for a list of test cases against one crawl result."""
        scripts: list[GeneratedScript] = []
        for tc in test_cases:
            try:
                script = self.generate(test_case=tc, crawl_result=crawl_result)
                scripts.append(script)
            except Exception as exc:  # noqa: BLE001
                self._log.error("generation_failed", test_id=tc.id, error=str(exc))
                scripts.append(
                    GeneratedScript(
                        test_id=tc.id,
                        framework=self.framework,
                        filename=f"test_{tc.id.lower()}_failed.py",
                        content=f"# Generation failed: {exc}\n",
                        warnings=[str(exc)],
                        success=False,
                    )
                )
        return scripts

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _call_llm(
        self,
        test_case: TestCase,
        crawl_result: CrawlResult,
        warnings: list[str],
    ) -> str:
        """Send prompt to LLM and return raw action lines string."""
        system_msg = SystemMessage(content=SYSTEM_PROMPT)
        user_msg = HumanMessage(content=_build_user_prompt(test_case, crawl_result))

        try:
            response = self.llm.invoke([system_msg, user_msg])
            # LangChain response.content can be str or list
            if isinstance(response.content, str):
                return response.content
            if isinstance(response.content, list):
                return " ".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in response.content
                )
            warnings.append(f"Unexpected LLM response type: {type(response.content)}")
            return ""
        except Exception as exc:  # noqa: BLE001
            self._log.error("llm_call_failed", error=str(exc))
            warnings.append(f"LLM call failed: {exc}")
            return ""

    def _save(self, script: GeneratedScript) -> None:
        """Write the script and conftest.py to the output directory."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        test_file = self.output_dir / script.filename
        test_file.write_text(script.content, encoding="utf-8")
        self._log.info("saved_test_file", path=str(test_file))

        conftest_file = self.output_dir / "conftest.py"
        if not conftest_file.exists():
            # Only write conftest once — don't overwrite if already exists
            conftest_file.write_text(script.conftest_content, encoding="utf-8")
            self._log.info("saved_conftest", path=str(conftest_file))
