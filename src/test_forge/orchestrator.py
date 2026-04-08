"""Orchestrator — coordinates Reader, Auth, Crawler, and Generator agents.

Entry point::

    python -m test_forge.orchestrator --input inputs/sample_test_cases.csv \\
                                      --url https://www.saucedemo.com

Or programmatically::

    from test_forge.orchestrator import Orchestrator
    result = orchestrator.run(input_file="inputs/sample_test_cases.csv",
                              target_url="https://www.saucedemo.com")
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field

import structlog

from test_forge.agents.auth_agent import AuthAgent, AuthError
from test_forge.agents.crawler_agent import CrawlerAgent
from test_forge.agents.generator_agent import GeneratorAgent
from test_forge.agents.reader_agent import ReaderAgent
from test_forge.config.settings import get_settings
from test_forge.core.models import CrawlResult, TestCase
from test_forge.frameworks.base_template import GeneratedScript

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------ #
# Result model                                                         #
# ------------------------------------------------------------------ #


@dataclass
class OrchestratorResult:
    """Summary of a full pipeline run."""

    input_file: str
    target_url: str
    framework: str

    total_test_cases: int = 0
    total_generated: int = 0
    total_failed: int = 0

    scripts: list[GeneratedScript] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    session_path: str = ""
    crawl_success: bool = True
    elapsed_seconds: float = 0.0

    @property
    def success(self) -> bool:
        return self.crawl_success and self.total_failed == 0

    @property
    def output_files(self) -> list[str]:
        return [s.filename for s in self.scripts if s.success]


# ------------------------------------------------------------------ #
# Orchestrator                                                         #
# ------------------------------------------------------------------ #


class Orchestrator:
    """Runs the full test-forge pipeline from input file to generated scripts.

    Usage::

        orch = Orchestrator(framework="playwright", output_dir="./outputs")
        result = orch.run(
            input_file="inputs/my_tests.csv",
            target_url="https://www.saucedemo.com",
        )
        print(result.total_generated)   # 5
        print(result.output_files)      # ['test_tc001_...py', ...]
    """

    def __init__(
        self,
        framework: str = "playwright",
        output_dir: str = "./outputs",
        use_auth: bool = True,
    ) -> None:
        self._settings = get_settings()
        self.framework = framework
        self.output_dir = output_dir
        self.use_auth = use_auth
        self._log = logger.bind(component="orchestrator")

        # Agents — instantiated once and reused across runs
        self._reader = ReaderAgent()
        self._auth = AuthAgent(output_dir=output_dir)
        self._crawler = CrawlerAgent()
        self._generator = GeneratorAgent(
            framework=framework,
            output_dir=output_dir,
            save_to_disk=True,
        )

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def run(
        self,
        *,
        input_file: str,
        target_url: str,
        force_auth_refresh: bool = False,
    ) -> OrchestratorResult:
        """Execute the full pipeline and return a result summary.

        Args:
            input_file: Path to the test case file (CSV, Excel, Word, PDF, MD).
            target_url: URL of the application under test to crawl.
            force_auth_refresh: Force re-login even if a valid session exists.

        Returns:
            OrchestratorResult with all generated scripts and pipeline metadata.
        """
        start = time.monotonic()
        self._log.info("pipeline_start", input_file=input_file, url=target_url)

        result = OrchestratorResult(
            input_file=input_file,
            target_url=target_url,
            framework=self.framework,
        )

        # ── Step 1: Read test cases ──────────────────────────────────
        test_cases = self._step_read(input_file, result)
        if not test_cases:
            result.elapsed_seconds = time.monotonic() - start
            return result

        result.total_test_cases = len(test_cases)

        # ── Step 2: Authenticate (optional) ─────────────────────────
        session_path = self._step_auth(result, force_auth_refresh)

        # ── Step 3: Crawl target URL ─────────────────────────────────
        crawl_result = self._step_crawl(target_url, session_path, result)
        if crawl_result is None:
            result.elapsed_seconds = time.monotonic() - start
            return result

        # ── Step 4: Generate scripts ─────────────────────────────────
        self._step_generate(test_cases, crawl_result, result)

        result.elapsed_seconds = round(time.monotonic() - start, 2)
        self._log.info(
            "pipeline_complete",
            generated=result.total_generated,
            failed=result.total_failed,
            elapsed=result.elapsed_seconds,
        )
        return result

    # ------------------------------------------------------------------ #
    # Pipeline steps                                                       #
    # ------------------------------------------------------------------ #

    def _step_read(
        self,
        input_file: str,
        result: OrchestratorResult,
    ) -> list[TestCase]:
        """Step 1 — Read and parse the input test case file."""
        self._log.info("step_read", file=input_file)
        try:
            read_result = self._reader.run(input_file)
            if read_result.warnings:
                result.warnings.extend(read_result.warnings)
            if not read_result.test_cases:
                result.warnings.append(f"No test cases found in '{input_file}'")
                return []
            self._log.info("read_complete", count=read_result.total_parsed)
            return list(read_result.test_cases)
        except Exception as exc:  # noqa: BLE001
            msg = f"Reader failed: {exc}"
            self._log.error("step_read_failed", error=str(exc))
            result.warnings.append(msg)
            return []

    def _step_auth(
        self,
        result: OrchestratorResult,
        force_refresh: bool,
    ) -> str:
        """Step 2 — Authenticate and return session path (empty string if skipped)."""
        if not self.use_auth:
            self._log.info("step_auth_skipped", reason="use_auth=False")
            return ""

        has_credentials = bool(self._settings.APP_USERNAME and self._settings.APP_PASSWORD)
        if not has_credentials:
            self._log.info("step_auth_skipped", reason="no credentials in settings")
            return ""

        self._log.info("step_auth", username=self._settings.APP_USERNAME)
        try:
            session_path = str(self._auth.authenticate_from_settings(force_refresh=force_refresh))
            result.session_path = session_path
            self._log.info("step_auth_complete", session=session_path)
            return session_path
        except AuthError as exc:
            msg = f"Auth failed (continuing without session): {exc}"
            self._log.warning("step_auth_failed", error=str(exc))
            result.warnings.append(msg)
            return ""

    def _step_crawl(
        self,
        target_url: str,
        session_path: str,
        result: OrchestratorResult,
    ) -> CrawlResult | None:
        """Step 3 — Crawl the target URL and return CrawlResult."""
        self._log.info("step_crawl", url=target_url)
        try:
            crawl_result = self._crawler.run(
                target_url,
                auth_state=session_path or None,
            )
            if crawl_result.warnings:
                result.warnings.extend(crawl_result.warnings)
            if not crawl_result.success:
                result.crawl_success = False
                result.warnings.append(f"Crawl failed for '{target_url}'")
            self._log.info(
                "step_crawl_complete",
                elements=len(crawl_result.elements),
                success=crawl_result.success,
            )
            return crawl_result
        except Exception as exc:  # noqa: BLE001
            msg = f"Crawler failed: {exc}"
            self._log.error("step_crawl_failed", error=str(exc))
            result.warnings.append(msg)
            result.crawl_success = False
            return None

    def _step_generate(
        self,
        test_cases: list[TestCase],
        crawl_result: CrawlResult,
        result: OrchestratorResult,
    ) -> None:
        """Step 4 — Generate a script for each test case."""
        self._log.info("step_generate", count=len(test_cases))

        scripts = self._generator.generate_batch(
            test_cases=test_cases,
            crawl_result=crawl_result,
        )

        for script in scripts:
            result.scripts.append(script)
            if script.success:
                result.total_generated += 1
            else:
                result.total_failed += 1
            if script.warnings:
                result.warnings.extend(script.warnings)

        self._log.info(
            "step_generate_complete",
            generated=result.total_generated,
            failed=result.total_failed,
        )

    # ------------------------------------------------------------------ #
    # Pretty report                                                        #
    # ------------------------------------------------------------------ #

    @staticmethod
    def print_report(result: OrchestratorResult) -> None:
        """Print a human-readable pipeline summary to stdout."""
        print("\n" + "=" * 60)
        print("  test-forge — Pipeline Report")
        print("=" * 60)
        print(f"  Input file  : {result.input_file}")
        print(f"  Target URL  : {result.target_url}")
        print(f"  Framework   : {result.framework}")
        print(f"  Elapsed     : {result.elapsed_seconds}s")
        print("-" * 60)
        print(f"  Test cases  : {result.total_test_cases}")
        print(f"  Generated   : {result.total_generated}")
        print(f"  Failed      : {result.total_failed}")
        print(f"  Status      : {'✓ SUCCESS' if result.success else '✗ FAILED'}")

        if result.session_path:
            print(f"  Session     : {result.session_path}")

        if result.output_files:
            print("\n  Output files:")
            for f in result.output_files:
                print(f"    - {f}")

        if result.warnings:
            print(f"\n  Warnings ({len(result.warnings)}):")
            for w in result.warnings:
                print(f"    ⚠  {w}")

        print("=" * 60 + "\n")


# ------------------------------------------------------------------ #
# CLI entry point                                                      #
# ------------------------------------------------------------------ #


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="test-forge — generate Playwright tests from manual test cases"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to test case file (CSV, Excel, Word, PDF, Markdown)",
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Target application URL to crawl",
    )
    parser.add_argument(
        "--framework",
        default="playwright",
        choices=["playwright"],
        help="Test framework to generate for (default: playwright)",
    )
    parser.add_argument(
        "--output-dir",
        default="./outputs",
        help="Output directory for generated scripts (default: ./outputs)",
    )
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help="Skip authentication step",
    )
    parser.add_argument(
        "--force-auth-refresh",
        action="store_true",
        help="Force re-login even if a valid session exists",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entry point — called by `python -m test_forge.orchestrator`."""
    args = _parse_args()

    orch = Orchestrator(
        framework=args.framework,
        output_dir=args.output_dir,
        use_auth=not args.no_auth,
    )

    result = orch.run(
        input_file=args.input,
        target_url=args.url,
        force_auth_refresh=args.force_auth_refresh,
    )

    Orchestrator.print_report(result)

    # Exit with non-zero code if pipeline failed — useful for CI
    raise SystemExit(0 if result.success else 1)


if __name__ == "__main__":
    main()
