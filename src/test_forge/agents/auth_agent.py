"""Auth Agent — authenticates against a web app and saves session state for the Crawler Agent."""

from __future__ import annotations

import time
from pathlib import Path

import structlog
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from test_forge.config.settings import get_settings

logger = structlog.get_logger(__name__)

# Default session validity in seconds (30 minutes)
_SESSION_TTL_SECONDS = 30 * 60


class AuthError(Exception):
    """Raised when authentication fails or cannot be verified."""


class AuthAgent:
    """Logs into a web application and saves Playwright session state.

    The saved session file (cookies + local storage) can be passed directly
    to the Crawler Agent via its ``auth_state`` parameter, allowing it to
    crawl protected pages without re-authenticating.

    Usage::

        agent = AuthAgent()
        session_path = agent.authenticate(
            login_url="https://www.saucedemo.com",
            username="standard_user",
            password="secret_sauce",
            username_selector="[data-test='username']",
            password_selector="[data-test='password']",
            submit_selector="[data-test='login-button']",
            success_url_fragment="/inventory.html",
        )
        # Pass session_path to CrawlerAgent
    """

    def __init__(
        self,
        output_dir: str = "./outputs",
        session_ttl_seconds: int = _SESSION_TTL_SECONDS,
    ) -> None:
        self._settings = get_settings()
        self.session_dir = Path(output_dir) / "sessions"
        self.session_ttl = session_ttl_seconds
        self._log = logger.bind(agent="auth")

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def authenticate(
        self,
        *,
        login_url: str,
        username: str,
        password: str,
        username_selector: str,
        password_selector: str,
        submit_selector: str,
        success_url_fragment: str,
        force_refresh: bool = False,
    ) -> str:
        """Log in and return path to the saved session file.

        If a valid (non-expired) session file already exists for this
        username, it is returned immediately without opening a browser —
        unless ``force_refresh=True``.

        Args:
            login_url: Full URL of the login page.
            username: Username / email to log in with.
            password: Password to log in with.
            username_selector: CSS/data-test selector for the username input.
            password_selector: CSS/data-test selector for the password input.
            submit_selector: CSS/data-test selector for the login button.
            success_url_fragment: URL substring that confirms successful login
                (e.g. "/inventory.html"). Auth fails if URL does not contain
                this after submit.
            force_refresh: Skip TTL check and always re-authenticate.

        Returns:
            Absolute path to the session JSON file.

        Raises:
            AuthError: If login fails or success URL is not reached.
        """
        session_path = self._session_path(username)

        if not force_refresh and self._session_valid(session_path):
            self._log.info("reusing_session", username=username, path=str(session_path))
            return str(session_path)

        self._log.info("authenticating", username=username, login_url=login_url)
        session_path = self._run_login(
            login_url=login_url,
            username=username,
            password=password,
            username_selector=username_selector,
            password_selector=password_selector,
            submit_selector=submit_selector,
            success_url_fragment=success_url_fragment,
            session_path=session_path,
        )
        self._log.info("authenticated", username=username, session=str(session_path))
        return str(session_path)

    def authenticate_from_settings(self, force_refresh: bool = False) -> str:
        """Convenience method — reads all credentials from settings.

        Uses: DEFAULT_BASE_URL, APP_USERNAME, APP_PASSWORD and the
        SauceDemo selector defaults. Override selectors explicitly for
        other apps.
        """
        return self.authenticate(
            login_url=self._settings.DEFAULT_BASE_URL,
            username=self._settings.APP_USERNAME,
            password=self._settings.APP_PASSWORD,
            username_selector="[data-test='username']",
            password_selector="[data-test='password']",
            submit_selector="[data-test='login-button']",
            success_url_fragment="/inventory.html",
            force_refresh=force_refresh,
        )

    def session_exists(self, username: str) -> bool:
        """Return True if a valid (non-expired) session exists for username."""
        return self._session_valid(self._session_path(username))

    def clear_session(self, username: str) -> None:
        """Delete the saved session file for a username."""
        path = self._session_path(username)
        if path.exists():
            path.unlink()
            self._log.info("session_cleared", username=username)

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _session_path(self, username: str) -> Path:
        """Return the expected session file path for a username."""
        safe = username.replace("@", "_at_").replace(".", "_")
        return self.session_dir / f"session_{safe}.json"

    def _session_valid(self, path: Path) -> bool:
        """Return True if session file exists and is within TTL."""
        if not path.exists():
            return False
        age = time.time() - path.stat().st_mtime
        return age < self.session_ttl

    def _run_login(
        self,
        *,
        login_url: str,
        username: str,
        password: str,
        username_selector: str,
        password_selector: str,
        submit_selector: str,
        success_url_fragment: str,
        session_path: Path,
    ) -> Path:
        """Open a real browser, log in, and save session state to disk."""
        self.session_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as pw:
            browser: Browser = pw.chromium.launch(
                headless=self._settings.CRAWLER_HEADLESS,
            )
            context: BrowserContext = browser.new_context()
            page: Page = context.new_page()

            try:
                # Navigate to login page
                page.goto(login_url, timeout=self._settings.CRAWLER_TIMEOUT_MS)
                self._log.debug("navigated_to_login", url=login_url)

                # Fill credentials
                page.fill(username_selector, username)
                page.fill(password_selector, password)

                # Submit
                page.click(submit_selector)

                # Wait for navigation
                page.wait_for_load_state("networkidle", timeout=self._settings.CRAWLER_TIMEOUT_MS)

                # Verify success
                current_url = page.url
                if success_url_fragment not in current_url:
                    # Check for common error selectors before giving up
                    error_text = self._extract_error(page)
                    raise AuthError(
                        f"Login failed for '{username}'. "
                        f"Expected URL to contain '{success_url_fragment}', "
                        f"got '{current_url}'. "
                        f"Page error: {error_text or 'none detected'}"
                    )

                # Save session (cookies + localStorage)
                context.storage_state(path=str(session_path))
                self._log.debug("session_saved", path=str(session_path))

            finally:
                context.close()
                browser.close()

        return session_path

    @staticmethod
    def _extract_error(page: Page) -> str:
        """Try to extract a visible error message from the login page."""
        # Common error selectors across frameworks
        candidates = [
            "[data-test='error']",
            ".error-message",
            ".alert",
            "[role='alert']",
            ".flash.error",
        ]
        for selector in candidates:
            try:
                el = page.locator(selector).first
                if el.is_visible():
                    return str(el.inner_text()).strip()
            except Exception:  # noqa: BLE001
                continue
        return ""
