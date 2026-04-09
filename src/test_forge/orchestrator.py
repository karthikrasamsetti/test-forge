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
from pathlib import Path

import structlog

from test_forge.agents.auth_agent import AuthAgent, AuthError
from test_forge.agents.crawler_agent import CrawlerAgent
from test_forge.agents.generator_agent import GeneratorAgent
from test_forge.agents.planner_agent import PlannerAgent
from test_forge.agents.reader_agent import ReaderAgent
from test_forge.agents.validator_agent import ValidatorAgent
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
        self._validator = ValidatorAgent()
        self._planner = PlannerAgent()

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
        crawl_map = self._step_crawl_all(test_cases, target_url, session_path, result)
        if not crawl_map:
            result.elapsed_seconds = time.monotonic() - start
            return result

        # ── Step 4: Generate scripts ─────────────────────────────────
        self._step_generate(test_cases, crawl_map, target_url, result)

        # ── Step 5: Validate generated scripts ───────────────────────
        self._step_validate(result)

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

    def _crawl_one(
        self,
        url: str,
        session_path: str,
        result: OrchestratorResult,
    ) -> CrawlResult | None:
        """Crawl a single URL and return CrawlResult, or None on failure."""
        self._log.info("step_crawl", url=url)
        try:
            crawl_result = self._crawler.run(
                url,
                auth_state=session_path or None,
            )
            if crawl_result.warnings:
                result.warnings.extend(crawl_result.warnings)
            if not crawl_result.success:
                result.crawl_success = False
                result.warnings.append(f"Crawl failed for '{url}'")
            self._log.info(
                "step_crawl_complete",
                url=url,
                elements=len(crawl_result.elements),
                success=crawl_result.success,
            )
            return crawl_result
        except Exception as exc:  # noqa: BLE001
            self._log.error("step_crawl_failed", url=url, error=str(exc))
            result.warnings.append(f"Crawler failed for '{url}': {exc}")
            result.crawl_success = False
            return None

    def _step_crawl_all(
        self,
        test_cases: list[TestCase],
        target_url: str,
        session_path: str,
        result: OrchestratorResult,
    ) -> dict[str, CrawlResult]:
        """Step 3 — Planner-driven crawl.

        For each test case, the PlannerAgent identifies which URLs it needs.
        All unique URLs are crawled once and cached. Each test case then gets
        a merged CrawlResult containing elements from all its needed pages.
        Returns a dict mapping test_case_id -> merged CrawlResult.
        """
        self._log.info("step_plan", count=len(test_cases))

        # Ask planner which URLs each test case needs
        plan: dict[str, list[str]] = {}
        for tc in test_cases:
            tc_base = tc.url.strip() if tc.url.strip() else target_url
            urls = self._planner.plan(test_case=tc, base_url=tc_base)
            plan[tc.id] = urls
            self._log.info("planned", test_id=tc.id, urls=urls)

        # Collect all unique URLs across all test cases
        all_urls: set[str] = set()
        for urls in plan.values():
            all_urls.update(urls)

        self._log.info("step_crawl_all", unique_urls=len(all_urls))

        # Crawl each unique URL exactly once
        url_cache: dict[str, CrawlResult] = {}
        for url in all_urls:
            cr = self._crawl_one(url, session_path, result)
            if cr is not None:
                url_cache[url] = cr

        if not url_cache:
            result.crawl_success = False
            result.warnings.append("All crawls failed — no selectors available")
            return {}

        # Build per-test-case merged CrawlResult
        crawl_map: dict[str, CrawlResult] = {}
        for tc in test_cases:
            tc_urls = plan.get(tc.id, [target_url])
            # Only include pages that actually returned elements
            tc_results = [
                url_cache[u] for u in tc_urls if u in url_cache and len(url_cache[u].elements) > 0
            ]

            if not tc_results:
                # Fallback to target_url crawl result, then any available
                fallback = url_cache.get(target_url) or next(
                    (cr for cr in url_cache.values() if len(cr.elements) > 0),
                    next(iter(url_cache.values())),
                )
                tc_results = [fallback]
                result.warnings.append(
                    f"Planned URLs for {tc.id} returned no elements — "
                    f"falling back to target_url crawl"
                )

            # Merge all pages for this test case into one CrawlResult
            base = tc_results[0]
            if len(tc_results) > 1:
                base = self._merge_crawl_results(base, tc_results[1:])

            crawl_map[tc.id] = base
            self._log.info(
                "crawl_merged",
                test_id=tc.id,
                pages=len(tc_results),
                elements=len(base.elements),
            )

        return crawl_map

    @staticmethod
    def _merge_crawl_results(
        base: CrawlResult,
        extras: list[CrawlResult],
    ) -> CrawlResult:
        """Merge elements from extra crawl results into the base CrawlResult.

        Deduplicates by selector so the same element isn't listed twice.
        The base URL and metadata are preserved — only elements are extended.
        """
        seen_selectors: set[str] = {el.selector for el in base.elements}
        merged_elements = list(base.elements)

        for extra in extras:
            for el in extra.elements:
                if el.selector not in seen_selectors:
                    merged_elements.append(el)
                    seen_selectors.add(el.selector)

        # Use model_copy to preserve all fields, only override elements
        return base.model_copy(update={"elements": merged_elements})

    def _step_generate(
        self,
        test_cases: list[TestCase],
        crawl_map: dict[str, CrawlResult],
        target_url: str,
        result: OrchestratorResult,
    ) -> None:
        """Step 4 — Generate a script per test case using its merged crawl result."""
        self._log.info("step_generate", count=len(test_cases))

        for tc in test_cases:
            # crawl_map is now keyed by test_case.id (from planner)
            crawl_result = crawl_map.get(tc.id) or next(iter(crawl_map.values()))

            try:
                script = self._generator.generate(
                    test_case=tc,
                    crawl_result=crawl_result,
                )
                result.scripts.append(script)
                if script.success:
                    result.total_generated += 1
                else:
                    result.total_failed += 1
                if script.warnings:
                    result.warnings.extend(script.warnings)
            except Exception as exc:  # noqa: BLE001
                self._log.error("generation_failed", test_id=tc.id, error=str(exc))
                result.total_failed += 1
                result.warnings.append(f"Generation failed for {tc.id}: {exc}")

        self._log.info(
            "step_generate_complete",
            generated=result.total_generated,
            failed=result.total_failed,
        )

    def _step_validate(self, result: OrchestratorResult) -> None:
        """Step 5 — Validate each generated script file on disk."""
        successful_scripts = [s for s in result.scripts if s.success]
        if not successful_scripts:
            return

        self._log.info("step_validate", count=len(successful_scripts))
        output_dir = Path(self.output_dir) / self.framework

        for script in successful_scripts:
            filepath = str(output_dir / script.filename)
            validation = self._validator.validate_file(filepath)

            if validation.errors:
                script.success = False
                script.warnings.extend(validation.errors)
                result.total_generated -= 1
                result.total_failed += 1
                self._log.warning(
                    "validation_failed",
                    file=script.filename,
                    errors=validation.errors,
                )
            if validation.warnings:
                script.warnings.extend(validation.warnings)
                result.warnings.extend(validation.warnings)

        self._log.info(
            "step_validate_complete",
            passed=result.total_generated,
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
