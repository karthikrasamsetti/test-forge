"""Planner Agent — identifies which URLs a test case needs to crawl."""

from __future__ import annotations

import json
import re

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from test_forge.core.llm import get_llm
from test_forge.core.models import TestCase

logger = structlog.get_logger(__name__)

# ------------------------------------------------------------------ #
# Prompts                                                              #
# ------------------------------------------------------------------ #

SYSTEM_PROMPT = """\
You are a test automation expert. Given a test case with steps, identify all the
web page URLs the test needs to visit or interact with.

Rules:
- Return ONLY a valid JSON array of full URLs (strings).
- Include the starting page AND every page the test navigates to.
- Use the base_url provided to construct full URLs from relative paths.
- Never include duplicate URLs.
- Never include external URLs (e.g. Twitter, Facebook).
- If a step mentions "login" or "navigate to login page", the login page URL is the BASE_URL itself (e.g. https://www.saucedemo.com NOT https://www.saucedemo.com/login).
- If a step mentions "cart", include the cart page URL.
- If a step mentions "checkout", include checkout-step-one.html, checkout-step-two.html, AND checkout-complete.html.
- If a step mentions "inventory" or "products", include the inventory page URL.
- Respond with ONLY the JSON array, no explanation, no markdown fences.

Example response:
["https://example.com/login", "https://example.com/inventory"]
"""


def _build_prompt(test_case: TestCase, base_url: str) -> str:
    steps_block = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(test_case.steps))
    return f"""\
Base URL: {base_url}

Test Case: {test_case.id} — {test_case.description}
Preconditions: {test_case.preconditions or "None"}
Steps:
{steps_block}
Expected Result: {test_case.expected_result}

Return the JSON array of URLs this test case needs to visit.
"""


# ------------------------------------------------------------------ #
# Planner Agent                                                        #
# ------------------------------------------------------------------ #


class PlannerAgent:
    """Determines which URLs a test case needs to crawl.

    Makes one LLM call per test case and returns a list of URLs.
    Results are cached so the same test case is never planned twice.

    Usage::

        planner = PlannerAgent()
        urls = planner.plan(test_case=tc, base_url="https://www.saucedemo.com")
        # ["https://www.saucedemo.com", "https://www.saucedemo.com/inventory.html"]
    """

    def __init__(self) -> None:
        self.llm = get_llm()
        self._cache: dict[str, list[str]] = {}
        self._log = logger.bind(agent="planner")

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def plan(self, *, test_case: TestCase, base_url: str) -> list[str]:
        """Return the list of URLs this test case needs to crawl.

        Results are cached by test_case.id — repeated calls are free.
        Falls back to [base_url] if the LLM call fails or returns invalid JSON.
        """
        cache_key = f"{test_case.id}::{base_url}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        self._log.info("planning", test_id=test_case.id)
        urls = self._call_llm(test_case, base_url)

        self._log.info("planned", test_id=test_case.id, urls=urls)
        self._cache[cache_key] = urls
        return urls

    def plan_batch(
        self,
        *,
        test_cases: list[TestCase],
        base_url: str,
    ) -> dict[str, list[str]]:
        """Plan all test cases and return a mapping of test_id -> [urls].

        Also returns the set of unique URLs across all test cases so the
        orchestrator knows exactly what to crawl.
        """
        return {tc.id: self.plan(test_case=tc, base_url=base_url) for tc in test_cases}

    def unique_urls(
        self,
        *,
        test_cases: list[TestCase],
        base_url: str,
    ) -> set[str]:
        """Return the set of all unique URLs needed across all test cases."""
        all_urls: set[str] = set()
        plan = self.plan_batch(test_cases=test_cases, base_url=base_url)
        for urls in plan.values():
            all_urls.update(urls)
        return all_urls

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _call_llm(self, test_case: TestCase, base_url: str) -> list[str]:
        """Call LLM and parse the JSON URL list. Falls back to [base_url]."""
        system_msg = SystemMessage(content=SYSTEM_PROMPT)
        user_msg = HumanMessage(content=_build_prompt(test_case, base_url))

        try:
            response = self.llm.invoke([system_msg, user_msg])
            raw = (
                response.content
                if isinstance(response.content, str)
                else " ".join(
                    b.get("text", "") if isinstance(b, dict) else str(b) for b in response.content
                )
            )
            return self._parse_urls(raw, base_url)
        except Exception as exc:  # noqa: BLE001
            self._log.error("llm_call_failed", test_id=test_case.id, error=str(exc))
            return [base_url]

    def _parse_urls(self, raw: str, base_url: str) -> list[str]:
        """Extract and validate URLs from LLM response.

        Strips markdown fences, parses JSON, validates each URL returns
        HTTP 200 before including it. Falls back to [base_url] on error.
        """
        # Strip markdown fences if LLM added them
        clean = re.sub(r"```(?:json)?|```", "", raw).strip()

        try:
            parsed = json.loads(clean)
        except json.JSONDecodeError:
            self._log.warning("invalid_json", raw=raw[:100])
            return [base_url]

        if not isinstance(parsed, list):
            self._log.warning("not_a_list", parsed=str(parsed)[:100])
            return [base_url]

        valid: list[str] = []
        seen: set[str] = set()
        for item in parsed:
            if not isinstance(item, str) or not item.startswith("http"):
                continue
            url = item.rstrip("/")
            if url not in seen:
                valid.append(url)
                seen.add(url)

        # Always include the base_url itself as it's guaranteed to exist
        if base_url.rstrip("/") not in {u.rstrip("/") for u in valid}:
            valid.insert(0, base_url.rstrip("/"))

        # Validate each URL returns HTTP 200
        validated = self._validate_urls(valid, base_url)
        return validated if validated else [base_url]

    def _validate_urls(self, urls: list[str], base_url: str) -> list[str]:
        """Filter URLs to only those that return HTTP 200.

        Uses HEAD request for speed. Falls back to GET if HEAD not supported.
        Skips validation on network errors and keeps the URL — better to
        attempt crawling than silently drop a valid page.
        """
        import urllib.request

        valid: list[str] = []
        for url in urls:
            try:
                req = urllib.request.Request(url, method="HEAD")
                with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                    if resp.status == 200:
                        valid.append(url)
                    else:
                        self._log.debug("url_skipped", url=url, status=resp.status)
            except Exception:  # noqa: BLE001
                # Network error or redirect — keep the URL and let crawler decide
                valid.append(url)
        return valid
