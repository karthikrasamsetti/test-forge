"""Validator Agent — checks generated Playwright test scripts for correctness."""

from __future__ import annotations

import ast
import py_compile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# ------------------------------------------------------------------ #
# Result model                                                         #
# ------------------------------------------------------------------ #


@dataclass
class ValidationResult:
    """Result of validating a single generated test script."""

    filename: str
    valid: bool = True
    errors: list[str] = field(default_factory=list)  # blocking issues
    warnings: list[str] = field(default_factory=list)  # non-blocking issues

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.valid = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


# ------------------------------------------------------------------ #
# Validator Agent                                                      #
# ------------------------------------------------------------------ #


class ValidatorAgent:
    """Validates generated test scripts at three levels.

    Level 1 — Syntax: py_compile catches invalid Python immediately.
    Level 2 — Structure: AST checks for test functions, imports, empty bodies.
    Level 3 — Content: text scan for LLM artefacts and bad selectors.

    Usage::

        agent = ValidatorAgent()
        result = agent.validate_file("outputs/playwright/test_tc001.py")
        if not result.valid:
            print(result.errors)
    """

    # Playwright sync API import that must be present
    REQUIRED_IMPORT = "playwright.sync_api"

    # Selenium methods that should never appear in Playwright scripts
    SELENIUM_METHODS = {
        "find_element",
        "find_elements",
        "send_keys",
        "get_attribute",
        "execute_script",
        "WebDriverWait",
        "expected_conditions",
    }

    # LLM artefact patterns that indicate a bad generation
    BAD_CONTENT_PATTERNS = [
        "# UNPARSED:",
        "# UNKNOWN ACTION:",
        "# ARG ERROR:",
        "```",
    ]

    def __init__(self) -> None:
        self._log = logger.bind(agent="validator")

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def validate_file(self, filepath: str) -> ValidationResult:
        """Validate a generated test script file on disk.

        Args:
            filepath: Absolute or relative path to the .py file.

        Returns:
            ValidationResult with errors and warnings.
        """
        path = Path(filepath)
        result = ValidationResult(filename=path.name)
        self._log.info("validating", file=path.name)

        if not path.exists():
            result.add_error(f"File not found: {filepath}")
            return result

        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            result.add_error(f"Cannot read file: {exc}")
            return result

        # Run all three levels — continue even if one fails so we
        # collect as many issues as possible in one pass
        self._check_syntax(source, path.name, result)
        if result.valid:
            # AST checks only make sense if syntax is valid
            self._check_structure(source, result)
        self._check_content(source, result)

        self._log.info(
            "validated",
            file=path.name,
            valid=result.valid,
            errors=len(result.errors),
            warnings=len(result.warnings),
        )
        return result

    def validate_source(self, source: str, filename: str = "<string>") -> ValidationResult:
        """Validate a script from a source string (no file required).

        Useful for validating GeneratedScript.content directly without
        saving to disk first.
        """
        result = ValidationResult(filename=filename)

        self._check_syntax(source, filename, result)
        if result.valid:
            self._check_structure(source, result)
        self._check_content(source, result)

        return result

    def validate_batch(self, filepaths: list[str]) -> list[ValidationResult]:
        """Validate multiple files and return all results."""
        results: list[ValidationResult] = []
        for fp in filepaths:
            results.append(self.validate_file(fp))
        return results

    # ------------------------------------------------------------------ #
    # Level 1 — Syntax                                                     #
    # ------------------------------------------------------------------ #

    def _check_syntax(self, source: str, filename: str, result: ValidationResult) -> None:
        """Use py_compile to catch syntax errors."""
        try:
            # Write to a temp file so py_compile can work on it
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                delete=False,
                encoding="utf-8",
            ) as tmp:
                tmp.write(source)
                tmp_path = tmp.name

            py_compile.compile(tmp_path, doraise=True)
            Path(tmp_path).unlink(missing_ok=True)

        except py_compile.PyCompileError as exc:
            # Strip the temp file path from the error message
            msg = str(exc).replace(tmp_path, filename)
            result.add_error(f"Syntax error: {msg}")
            Path(tmp_path).unlink(missing_ok=True)

    # ------------------------------------------------------------------ #
    # Level 2 — Structure (AST)                                            #
    # ------------------------------------------------------------------ #

    def _check_structure(self, source: str, result: ValidationResult) -> None:
        """Parse AST and check structural correctness."""
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            result.add_error(f"AST parse failed: {exc}")
            return

        self._check_has_test_function(tree, result)
        self._check_playwright_import(tree, result)
        self._check_no_empty_test_body(tree, result)
        self._check_no_selenium_methods(tree, result)

    def _check_has_test_function(self, tree: ast.Module, result: ValidationResult) -> None:
        """Ensure at least one function starting with 'test_' exists."""
        test_functions = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        ]
        if not test_functions:
            result.add_error(
                "No test function found — expected at least one function named 'test_*'"
            )

    def _check_playwright_import(self, tree: ast.Module, result: ValidationResult) -> None:
        """Ensure playwright.sync_api is imported."""
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)

        if not any(self.REQUIRED_IMPORT in imp for imp in imports):
            result.add_warning(
                f"Missing import from '{self.REQUIRED_IMPORT}' — test may not run correctly"
            )

    def _check_no_empty_test_body(self, tree: ast.Module, result: ValidationResult) -> None:
        """Warn if a test function body contains only 'pass'."""
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not node.name.startswith("test_"):
                continue

            # Body is empty if it contains only Pass or a docstring + Pass
            real_stmts = [
                s
                for s in node.body
                if not isinstance(s, ast.Pass | ast.Expr)
                or (isinstance(s, ast.Expr) and not isinstance(s.value, ast.Constant))
            ]
            has_pass = any(isinstance(s, ast.Pass) for s in node.body)
            if has_pass and not real_stmts:
                result.add_warning(
                    f"Test function '{node.name}' has an empty body (only "
                    "'pass') — generator may have produced no actions"
                )

    def _check_no_selenium_methods(self, tree: ast.Module, result: ValidationResult) -> None:
        """Warn if any Selenium-specific method calls are detected."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # Check attribute calls like driver.find_element(...)
                if isinstance(node.func, ast.Attribute) and node.func.attr in self.SELENIUM_METHODS:
                    result.add_error(
                        f"Selenium method detected: '{node.func.attr}' — use Playwright API instead"
                    )

    # ------------------------------------------------------------------ #
    # Level 3 — Content (text scan)                                        #
    # ------------------------------------------------------------------ #

    def _check_content(self, source: str, result: ValidationResult) -> None:
        """Scan raw source text for LLM artefacts and bad patterns."""
        self._check_bad_patterns(source, result)
        self._check_empty_selectors(source, result)

    def _check_bad_patterns(self, source: str, result: ValidationResult) -> None:
        """Flag lines containing known LLM generation artefacts."""
        for line_no, line in enumerate(source.splitlines(), start=1):
            for pattern in self.BAD_CONTENT_PATTERNS:
                if pattern in line:
                    if pattern == "```":
                        result.add_warning(
                            f"Line {line_no}: LLM markdown fence detected "
                            f"({line.strip()!r}) — strip before running"
                        )
                    else:
                        result.add_error(
                            f"Line {line_no}: LLM artefact detected ({line.strip()!r})"
                        )

    def _check_empty_selectors(self, source: str, result: ValidationResult) -> None:
        """Warn if any page action is called with an empty string selector."""
        playwright_actions = {"fill", "click", "locator", "select_option"}
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return  # already caught in level 1

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in playwright_actions:
                continue
            # Check if first positional arg is an empty string
            if node.args and isinstance(node.args[0], ast.Constant):
                if node.args[0].value == "":
                    result.add_error(
                        f"Line {node.lineno}: Empty selector passed to 'page.{node.func.attr}()'"
                    )
