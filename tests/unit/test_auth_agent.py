"""Unit tests for AuthAgent — Playwright browser calls are fully mocked."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from test_forge.agents.auth_agent import AuthAgent, AuthError

# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #


def _make_agent(tmp_path: Path) -> AuthAgent:
    return AuthAgent(output_dir=str(tmp_path), session_ttl_seconds=1800)


def _write_session(path: Path, content: str = "{}") -> None:
    """Write a fake session file at the given path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ------------------------------------------------------------------ #
# Fixtures                                                             #
# ------------------------------------------------------------------ #


@pytest.fixture
def agent(tmp_path: Path) -> AuthAgent:
    return _make_agent(tmp_path)


@pytest.fixture
def mock_playwright():
    """Patch sync_playwright so no real browser is launched."""
    with patch("test_forge.agents.auth_agent.sync_playwright") as mock_pw_ctx:
        # Build the mock chain: sync_playwright().__enter__() -> pw
        pw = MagicMock()
        browser = MagicMock()
        context = MagicMock()
        page = MagicMock()

        mock_pw_ctx.return_value.__enter__.return_value = pw
        pw.chromium.launch.return_value = browser
        browser.new_context.return_value = context
        context.new_page.return_value = page

        # Default: successful login redirects to /inventory.html
        page.url = "https://www.saucedemo.com/inventory.html"
        page.wait_for_load_state.return_value = None

        yield {
            "pw": pw,
            "browser": browser,
            "context": context,
            "page": page,
        }


# ------------------------------------------------------------------ #
# Session path and TTL logic                                           #
# ------------------------------------------------------------------ #


class TestSessionPath:
    def test_session_path_uses_username(self, agent: AuthAgent, tmp_path: Path) -> None:
        path = agent._session_path("standard_user")
        assert "standard_user" in path.name
        assert path.suffix == ".json"

    def test_session_path_sanitizes_email(self, agent: AuthAgent) -> None:
        path = agent._session_path("user@example.com")
        assert "@" not in path.name
        assert "." not in path.stem

    def test_session_path_inside_output_dir(self, agent: AuthAgent, tmp_path: Path) -> None:
        path = agent._session_path("user")
        assert str(tmp_path) in str(path)


class TestSessionValidity:
    def test_returns_false_when_file_missing(self, agent: AuthAgent, tmp_path: Path) -> None:
        path = tmp_path / "sessions" / "session_nobody.json"
        assert agent._session_valid(path) is False

    def test_returns_true_for_fresh_file(self, agent: AuthAgent, tmp_path: Path) -> None:
        path = tmp_path / "sessions" / "session_user.json"
        _write_session(path)
        assert agent._session_valid(path) is True

    def test_returns_false_for_expired_file(self, tmp_path: Path) -> None:
        agent = AuthAgent(output_dir=str(tmp_path), session_ttl_seconds=1)
        path = tmp_path / "sessions" / "session_user.json"
        _write_session(path)
        time.sleep(1.1)
        assert agent._session_valid(path) is False

    def test_session_exists_true_for_fresh(self, agent: AuthAgent, tmp_path: Path) -> None:
        path = agent._session_path("standard_user")
        _write_session(path)
        assert agent.session_exists("standard_user") is True

    def test_session_exists_false_when_missing(self, agent: AuthAgent) -> None:
        assert agent.session_exists("nobody") is False


# ------------------------------------------------------------------ #
# authenticate — reuse path                                            #
# ------------------------------------------------------------------ #


class TestAuthenticateReuse:
    def test_reuses_valid_session_without_browser(self, agent: AuthAgent, tmp_path: Path) -> None:
        path = agent._session_path("standard_user")
        _write_session(path)

        with patch("test_forge.agents.auth_agent.sync_playwright") as mock_pw:
            result = agent.authenticate(
                login_url="https://example.com",
                username="standard_user",
                password="secret_sauce",
                username_selector="#u",
                password_selector="#p",
                submit_selector="#btn",
                success_url_fragment="/inventory.html",
            )
            # Browser should NOT have been launched
            mock_pw.assert_not_called()

        assert result == str(path)

    def test_force_refresh_ignores_valid_session(
        self, agent: AuthAgent, tmp_path: Path, mock_playwright: dict
    ) -> None:
        path = agent._session_path("standard_user")
        _write_session(path)

        agent.authenticate(
            login_url="https://example.com",
            username="standard_user",
            password="secret_sauce",
            username_selector="#u",
            password_selector="#p",
            submit_selector="#btn",
            success_url_fragment="/inventory.html",
            force_refresh=True,
        )
        # Browser MUST have been launched despite valid session
        mock_playwright["pw"].chromium.launch.assert_called_once()


# ------------------------------------------------------------------ #
# authenticate — browser interactions                                  #
# ------------------------------------------------------------------ #


class TestAuthenticateBrowser:
    def test_fills_username(self, agent: AuthAgent, mock_playwright: dict) -> None:
        agent.authenticate(
            login_url="https://www.saucedemo.com",
            username="standard_user",
            password="secret_sauce",
            username_selector="[data-test='username']",
            password_selector="[data-test='password']",
            submit_selector="[data-test='login-button']",
            success_url_fragment="/inventory.html",
        )
        page = mock_playwright["page"]
        page.fill.assert_any_call("[data-test='username']", "standard_user")

    def test_fills_password(self, agent: AuthAgent, mock_playwright: dict) -> None:
        agent.authenticate(
            login_url="https://www.saucedemo.com",
            username="standard_user",
            password="secret_sauce",
            username_selector="[data-test='username']",
            password_selector="[data-test='password']",
            submit_selector="[data-test='login-button']",
            success_url_fragment="/inventory.html",
        )
        page = mock_playwright["page"]
        page.fill.assert_any_call("[data-test='password']", "secret_sauce")

    def test_clicks_submit(self, agent: AuthAgent, mock_playwright: dict) -> None:
        agent.authenticate(
            login_url="https://www.saucedemo.com",
            username="standard_user",
            password="secret_sauce",
            username_selector="[data-test='username']",
            password_selector="[data-test='password']",
            submit_selector="[data-test='login-button']",
            success_url_fragment="/inventory.html",
        )
        mock_playwright["page"].click.assert_called_once_with("[data-test='login-button']")

    def test_saves_session_state(
        self, agent: AuthAgent, mock_playwright: dict, tmp_path: Path
    ) -> None:
        agent.authenticate(
            login_url="https://www.saucedemo.com",
            username="standard_user",
            password="secret_sauce",
            username_selector="[data-test='username']",
            password_selector="[data-test='password']",
            submit_selector="[data-test='login-button']",
            success_url_fragment="/inventory.html",
        )
        # context.storage_state must have been called with the session path
        mock_playwright["context"].storage_state.assert_called_once()
        call_kwargs = mock_playwright["context"].storage_state.call_args.kwargs
        assert "session_standard_user.json" in call_kwargs["path"]

    def test_closes_browser_on_success(self, agent: AuthAgent, mock_playwright: dict) -> None:
        agent.authenticate(
            login_url="https://www.saucedemo.com",
            username="standard_user",
            password="secret_sauce",
            username_selector="[data-test='username']",
            password_selector="[data-test='password']",
            submit_selector="[data-test='login-button']",
            success_url_fragment="/inventory.html",
        )
        mock_playwright["browser"].close.assert_called_once()
        mock_playwright["context"].close.assert_called_once()

    def test_closes_browser_on_failure(self, agent: AuthAgent, mock_playwright: dict) -> None:
        """Browser must close even when auth fails."""
        mock_playwright["page"].url = "https://www.saucedemo.com"  # stayed on login page

        with pytest.raises(AuthError):
            agent.authenticate(
                login_url="https://www.saucedemo.com",
                username="locked_out_user",
                password="secret_sauce",
                username_selector="[data-test='username']",
                password_selector="[data-test='password']",
                submit_selector="[data-test='login-button']",
                success_url_fragment="/inventory.html",
            )

        mock_playwright["browser"].close.assert_called_once()
        mock_playwright["context"].close.assert_called_once()

    def test_returns_session_path_string(self, agent: AuthAgent, mock_playwright: dict) -> None:
        result = agent.authenticate(
            login_url="https://www.saucedemo.com",
            username="standard_user",
            password="secret_sauce",
            username_selector="[data-test='username']",
            password_selector="[data-test='password']",
            submit_selector="[data-test='login-button']",
            success_url_fragment="/inventory.html",
        )
        assert isinstance(result, str)
        assert result.endswith(".json")


# ------------------------------------------------------------------ #
# AuthError cases                                                      #
# ------------------------------------------------------------------ #


class TestAuthError:
    def test_raises_when_url_does_not_change(self, agent: AuthAgent, mock_playwright: dict) -> None:
        mock_playwright["page"].url = "https://www.saucedemo.com"  # still on login

        with pytest.raises(AuthError, match="Login failed"):
            agent.authenticate(
                login_url="https://www.saucedemo.com",
                username="locked_out_user",
                password="secret_sauce",
                username_selector="[data-test='username']",
                password_selector="[data-test='password']",
                submit_selector="[data-test='login-button']",
                success_url_fragment="/inventory.html",
            )

    def test_error_message_includes_username(self, agent: AuthAgent, mock_playwright: dict) -> None:
        mock_playwright["page"].url = "https://www.saucedemo.com"

        with pytest.raises(AuthError, match="locked_out_user"):
            agent.authenticate(
                login_url="https://www.saucedemo.com",
                username="locked_out_user",
                password="secret_sauce",
                username_selector="[data-test='username']",
                password_selector="[data-test='password']",
                submit_selector="[data-test='login-button']",
                success_url_fragment="/inventory.html",
            )

    def test_error_includes_page_error_text(self, agent: AuthAgent, mock_playwright: dict) -> None:
        mock_playwright["page"].url = "https://www.saucedemo.com"
        # Simulate error element visible on page
        error_locator = MagicMock()
        error_locator.is_visible.return_value = True
        error_locator.inner_text.return_value = (
            "Epic sadface: Sorry, this user has been locked out."
        )
        mock_playwright["page"].locator.return_value.first = error_locator

        with pytest.raises(AuthError, match="Epic sadface"):
            agent.authenticate(
                login_url="https://www.saucedemo.com",
                username="locked_out_user",
                password="secret_sauce",
                username_selector="[data-test='username']",
                password_selector="[data-test='password']",
                submit_selector="[data-test='login-button']",
                success_url_fragment="/inventory.html",
            )


# ------------------------------------------------------------------ #
# clear_session                                                        #
# ------------------------------------------------------------------ #


class TestClearSession:
    def test_deletes_session_file(self, agent: AuthAgent, tmp_path: Path) -> None:
        path = agent._session_path("standard_user")
        _write_session(path)
        assert path.exists()
        agent.clear_session("standard_user")
        assert not path.exists()

    def test_no_error_when_file_missing(self, agent: AuthAgent) -> None:
        # Should not raise
        agent.clear_session("nobody")


# ------------------------------------------------------------------ #
# authenticate_from_settings                                           #
# ------------------------------------------------------------------ #


class TestAuthenticateFromSettings:
    def test_calls_authenticate_with_settings_values(
        self, agent: AuthAgent, mock_playwright: dict
    ) -> None:
        with patch.object(agent, "authenticate", return_value="/fake/path.json") as mock_auth:
            agent.authenticate_from_settings()
            mock_auth.assert_called_once()
            kwargs = mock_auth.call_args.kwargs
            # Settings defaults: standard_user / secret_sauce / saucedemo
            assert kwargs["username"] == agent._settings.APP_USERNAME
            assert kwargs["password"] == agent._settings.APP_PASSWORD
            assert kwargs["login_url"] == agent._settings.DEFAULT_BASE_URL
