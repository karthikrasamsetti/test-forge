"""Unit tests for PlannerAgent — LLM calls fully mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from test_forge.agents.planner_agent import PlannerAgent
from test_forge.core.models import TestCase

# ------------------------------------------------------------------ #
# Fixtures                                                             #
# ------------------------------------------------------------------ #


@pytest.fixture
def tc_login() -> TestCase:
    return TestCase(
        id="TC001",
        description="Login with valid credentials",
        steps=["Navigate to login page", "Enter credentials", "Click login"],
        expected_result="Redirected to inventory",
        preconditions="None",
    )


@pytest.fixture
def tc_checkout() -> TestCase:
    return TestCase(
        id="TC005",
        description="Complete checkout flow",
        steps=[
            "Login with valid credentials",
            "Add product to cart",
            "Click cart icon",
            "Click Checkout",
            "Enter first name John",
            "Enter last name Doe",
            "Enter zip code 12345",
            "Click Continue",
            "Click Finish",
        ],
        expected_result="Order confirmation shown",
        preconditions="User is logged in with item in cart",
    )


def _make_agent(llm_response: str) -> PlannerAgent:
    """Build a PlannerAgent with a mocked LLM returning llm_response."""
    with patch("test_forge.agents.planner_agent.get_llm") as mock_get_llm:
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = llm_response
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        agent = PlannerAgent()
        agent.llm = mock_llm
    return agent


# ------------------------------------------------------------------ #
# URL parsing                                                          #
# ------------------------------------------------------------------ #


class TestUrlParsing:
    """Tests for _parse_urls — _validate_urls is patched to return URLs as-is."""

    def _parse(self, agent: PlannerAgent, raw: str, base_url: str) -> list[str]:
        """Call _parse_urls with _validate_urls stubbed out."""
        with patch.object(agent, "_validate_urls", side_effect=lambda urls, _: list(urls)):
            return list(agent._parse_urls(raw, base_url))

    def test_parses_valid_json_array(self) -> None:
        agent = _make_agent('["https://example.com/login"]')
        result = self._parse(agent, '["https://example.com/login"]', "https://example.com")
        assert "https://example.com/login" in result
        assert "https://example.com" in result

    def test_parses_multiple_urls(self) -> None:
        agent = _make_agent("")
        raw = '["https://example.com/login", "https://example.com/inventory"]'
        result = self._parse(agent, raw, "https://example.com")
        assert len(result) == 3  # base_url + 2 planned urls

    def test_strips_markdown_fences(self) -> None:
        agent = _make_agent("")
        raw = '```json\n["https://example.com/login"]\n```'
        result = self._parse(agent, raw, "https://example.com")
        assert "https://example.com/login" in result
        assert "https://example.com" in result

    def test_invalid_json_falls_back_to_base_url(self) -> None:
        agent = _make_agent("")
        result = self._parse(agent, "not json at all", "https://example.com")
        assert result == ["https://example.com"]

    def test_non_list_json_falls_back(self) -> None:
        agent = _make_agent("")
        result = self._parse(agent, '{"url": "https://example.com"}', "https://example.com")
        assert result == ["https://example.com"]

    def test_filters_non_http_urls(self) -> None:
        agent = _make_agent("")
        raw = '["https://example.com/login", "ftp://bad.com", "/relative"]'
        result = self._parse(agent, raw, "https://example.com")
        assert "https://example.com/login" in result
        assert "https://example.com" in result

    def test_empty_list_falls_back(self) -> None:
        agent = _make_agent("")
        result = self._parse(agent, "[]", "https://example.com")
        assert result == ["https://example.com"]

    def test_trailing_slash_stripped(self) -> None:
        agent = _make_agent("")
        result = self._parse(agent, '["https://example.com/login/"]', "https://example.com")
        assert "https://example.com/login" in result
        assert "https://example.com" in result


# ------------------------------------------------------------------ #
# plan() — happy path                                                  #
# ------------------------------------------------------------------ #


class TestPlanHappyPath:
    def test_returns_list_of_urls(self, tc_login: TestCase) -> None:
        agent = _make_agent('["https://www.saucedemo.com"]')
        result = agent.plan(test_case=tc_login, base_url="https://www.saucedemo.com")
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_single_page_test_returns_one_url(self, tc_login: TestCase) -> None:
        agent = _make_agent('["https://www.saucedemo.com"]')
        result = agent.plan(test_case=tc_login, base_url="https://www.saucedemo.com")
        assert result == ["https://www.saucedemo.com"]

    def test_multi_page_test_returns_multiple_urls(self, tc_checkout: TestCase) -> None:
        response = '["https://www.saucedemo.com/inventory.html", "https://www.saucedemo.com/cart.html", "https://www.saucedemo.com/checkout-step-one.html"]'
        agent = _make_agent(response)
        result = agent.plan(test_case=tc_checkout, base_url="https://www.saucedemo.com")
        assert len(result) == 4  # base_url + 3 planned urls

    def test_llm_called_once(self, tc_login: TestCase) -> None:
        agent = _make_agent('["https://www.saucedemo.com"]')
        agent.plan(test_case=tc_login, base_url="https://www.saucedemo.com")
        assert agent.llm.invoke.call_count == 1

    def test_llm_failure_falls_back_to_base_url(self, tc_login: TestCase) -> None:
        with patch("test_forge.agents.planner_agent.get_llm") as mock_get_llm:
            mock_llm = MagicMock()
            mock_llm.invoke.side_effect = RuntimeError("API down")
            mock_get_llm.return_value = mock_llm
            agent = PlannerAgent()
            agent.llm = mock_llm

        result = agent.plan(test_case=tc_login, base_url="https://www.saucedemo.com")
        assert result == ["https://www.saucedemo.com"]


# ------------------------------------------------------------------ #
# Caching                                                              #
# ------------------------------------------------------------------ #


class TestCaching:
    def test_same_test_case_not_called_twice(self, tc_login: TestCase) -> None:
        agent = _make_agent('["https://www.saucedemo.com"]')
        agent.plan(test_case=tc_login, base_url="https://www.saucedemo.com")
        agent.plan(test_case=tc_login, base_url="https://www.saucedemo.com")
        assert agent.llm.invoke.call_count == 1

    def test_different_test_cases_both_called(
        self, tc_login: TestCase, tc_checkout: TestCase
    ) -> None:
        agent = _make_agent('["https://www.saucedemo.com"]')
        agent.plan(test_case=tc_login, base_url="https://www.saucedemo.com")
        agent.plan(test_case=tc_checkout, base_url="https://www.saucedemo.com")
        assert agent.llm.invoke.call_count == 2

    def test_different_base_url_triggers_new_call(self, tc_login: TestCase) -> None:
        agent = _make_agent('["https://www.saucedemo.com"]')
        agent.plan(test_case=tc_login, base_url="https://www.saucedemo.com")
        agent.plan(test_case=tc_login, base_url="https://other.com")
        assert agent.llm.invoke.call_count == 2


# ------------------------------------------------------------------ #
# plan_batch                                                           #
# ------------------------------------------------------------------ #


class TestPlanBatch:
    def test_returns_dict_keyed_by_test_id(self, tc_login: TestCase, tc_checkout: TestCase) -> None:
        agent = _make_agent('["https://www.saucedemo.com"]')
        result = agent.plan_batch(
            test_cases=[tc_login, tc_checkout],
            base_url="https://www.saucedemo.com",
        )
        assert "TC001" in result
        assert "TC005" in result

    def test_empty_list_returns_empty_dict(self) -> None:
        agent = _make_agent('["https://www.saucedemo.com"]')
        result = agent.plan_batch(test_cases=[], base_url="https://www.saucedemo.com")
        assert result == {}


# ------------------------------------------------------------------ #
# unique_urls                                                          #
# ------------------------------------------------------------------ #


class TestUniqueUrls:
    def test_deduplicates_across_test_cases(
        self, tc_login: TestCase, tc_checkout: TestCase
    ) -> None:
        # Both return same URL — should only appear once
        agent = _make_agent('["https://www.saucedemo.com/inventory.html"]')
        result = agent.unique_urls(
            test_cases=[tc_login, tc_checkout],
            base_url="https://www.saucedemo.com",
        )
        assert isinstance(result, set)
        assert "https://www.saucedemo.com/inventory.html" in result
        assert len(result) == 2  # base_url + inventory.html (deduplicated)

    def test_returns_set(self, tc_login: TestCase) -> None:
        agent = _make_agent('["https://www.saucedemo.com"]')
        result = agent.unique_urls(
            test_cases=[tc_login],
            base_url="https://www.saucedemo.com",
        )
        assert isinstance(result, set)
