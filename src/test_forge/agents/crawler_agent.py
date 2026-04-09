"""
Crawler Agent — visits a URL with a real browser and extracts
interactive elements, selectors, and page metadata.

Uses Playwright sync API for simplicity.
Supports public pages and auth-protected pages (via auth_state).
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from test_forge.config.settings import get_settings
from test_forge.core.models import (
    CrawlResult,
    FormInfo,
    PageElement,
    PageMetadata,
)

log = structlog.get_logger(__name__)
settings = get_settings()


# ── Selector strategy ──────────────────────────────────────────────────────────
def get_best_selector(element_info: dict) -> tuple[str, str]:
    """
    Picks the most stable selector for an element.
    Returns (selector, strategy_name).

    Priority:
    1. data-test attribute  — explicitly for testing, most stable
    2. id attribute         — stable unless auto-generated
    3. aria-label           — semantic and stable
    4. name attribute       — stable for form inputs
    5. placeholder          — readable but can change
    6. tag + type combo     — reasonable fallback
    7. CSS class            — fragile, last resort
    """
    attrs = element_info.get("attributes", {})
    tag = element_info.get("tag", "")
    el_id = element_info.get("id", "")
    el_name = element_info.get("name", "")
    el_type = element_info.get("type", "")
    placeholder = element_info.get("placeholder", "")
    text = element_info.get("text", "").strip()
    classes = element_info.get("classes", [])

    # 1. data-test / data-testid / data-cy
    for attr in ("data-test", "data-testid", "data-cy", "data-automation"):
        if attr in attrs and attrs[attr]:
            return f"[{attr}='{attrs[attr]}']", "data-test"

    # 2. ID — only if it doesn't look auto-generated
    if el_id and not _looks_auto_generated(el_id):
        return f"#{el_id}", "id"

    # 3. aria-label
    aria_label = attrs.get("aria-label", "")
    if aria_label:
        return f"[aria-label='{aria_label}']", "aria"

    # 4. name attribute (great for form inputs)
    if el_name:
        return f"[name='{el_name}']", "name"

    # 5. placeholder (readable, ok for inputs)
    if placeholder:
        return f"[placeholder='{placeholder}']", "placeholder"

    # 6. button or link with text
    if tag in ("button", "a") and text:
        clean_text = text[:30].strip()
        return f"{tag}:has-text('{clean_text}')", "text"

    # 7. type + tag combo
    if el_type:
        return f"{tag}[type='{el_type}']", "css"

    # 8. First non-dynamic class
    for cls in classes:
        if not _looks_auto_generated(cls):
            return f".{cls}", "css"

    # Fallback
    return tag, "css"


def _looks_auto_generated(value: str) -> bool:
    """
    Heuristic — IDs/classes like 'a3f9bc12' or 'css-1x2y3z' are likely
    auto-generated and will break when the app rebuilds.
    """
    import re

    # Mostly hex digits → auto-generated
    if re.match(r"^[0-9a-f]{6,}$", value, re.IGNORECASE):
        return True
    # Contains random-looking suffix
    if re.search(r"[-_][0-9a-f]{4,}$", value, re.IGNORECASE):
        return True
    return False


# ── SPA detection ──────────────────────────────────────────────────────────────
def detect_framework(page: Any) -> tuple[bool, str]:  # type: ignore[name-defined]
    """
    Detects if the page uses a JS framework.
    Returns (is_spa, framework_name).
    """
    try:
        result = page.evaluate("""() => {
            if (window.__REACT_DEVTOOLS_GLOBAL_HOOK__ ||
                document.querySelector('[data-reactroot]') ||
                document.querySelector('#root') ||
                document.querySelector('#app')) {
                return 'react';
            }
            if (window.__vue_devtools_global_hook__ ||
                document.querySelector('[data-v-app]')) {
                return 'vue';
            }
            if (window.getAllAngularRootElements ||
                document.querySelector('[ng-version]')) {
                return 'angular';
            }
            return 'unknown';
        }""")
        is_spa = result in ("react", "vue", "angular")
        return is_spa, result
    except Exception:
        return False, "unknown"


# ── Element extraction ─────────────────────────────────────────────────────────
def extract_elements(page: Any) -> list[dict]:  # type: ignore[name-defined]
    """
    Extracts all interactive elements from the page DOM.
    Returns raw dicts — converted to PageElement after selector picking.
    """
    try:
        elements = page.evaluate("""() => {
            const results = [];
            const selectors = [
                'input', 'button', 'a[href]', 'select',
                'textarea', '[role="button"]', '[onclick]',
                '[data-test]', '[data-testid]'
            ];

            const seen = new Set();

            selectors.forEach(sel => {
                document.querySelectorAll(sel).forEach(el => {
                    if (seen.has(el)) return;
                    seen.add(el);

                    const rect = el.getBoundingClientRect();
                    const attrs = {};
                    for (const attr of el.attributes) {
                        if (attr.name.startsWith('data-') ||
                            attr.name.startsWith('aria-')) {
                            attrs[attr.name] = attr.value;
                        }
                    }

                    results.push({
                        tag:         el.tagName.toLowerCase(),
                        id:          el.id || '',
                        name:        el.name || el.getAttribute('name') || '',
                        type:        el.type || el.getAttribute('type') || '',
                        placeholder: el.placeholder || '',
                        text:        el.innerText?.trim().slice(0, 100) || '',
                        value:       el.value || '',
                        href:        el.href || '',
                        classes:     Array.from(el.classList),
                        attributes:  attrs,
                        is_visible:  rect.width > 0 && rect.height > 0,
                        is_enabled:  !el.disabled,
                        role:        el.getAttribute('role') || '',
                    });
                });
            });

            return results;
        }""")
        return elements or []
    except Exception as e:
        log.warning("element_extraction_error", error=str(e))
        return []


def extract_label_for_input(page: Any, selector: str) -> str:
    """Finds the label text associated with an input element."""
    try:
        result = page.evaluate(f"""() => {{
            const el = document.querySelector("{selector}");
            if (!el) return '';
            if (el.id) {{
                const label = document.querySelector(`label[for="${{el.id}}"]`);
                if (label) return label.innerText.trim();
            }}
            const parent = el.closest('label');
            if (parent) return parent.innerText.trim();
            return el.getAttribute('aria-label') || '';
        }}""")
        return str(result) if result else ""
    except Exception:
        return ""


def detect_navigation_triggers(page: Any, elements: list[dict]) -> set[int]:  # type: ignore[name-defined]
    """
    Identifies which element indices are likely to trigger navigation.
    Checks href, type=submit, role=link.
    """
    nav_indices = set()
    for i, el in enumerate(elements):
        if el.get("href") and el.get("href") != "javascript:void(0)":
            nav_indices.add(i)
        if el.get("type") == "submit":
            nav_indices.add(i)
        if el.get("tag") == "button" and "submit" in str(el.get("classes", [])):
            nav_indices.add(i)
    return nav_indices


def extract_forms(page: Any, all_elements: list[PageElement]) -> list[FormInfo]:  # type: ignore[name-defined]
    """Extracts form structures from the page."""
    try:
        forms_data = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('form')).map(form => {
                const rect = form.getBoundingClientRect();
                return {
                    selector: form.id ? '#' + form.id : 'form',
                    action:   form.action || '',
                    method:   form.method || 'get',
                    field_ids: Array.from(
                        form.querySelectorAll('input, select, textarea')
                    ).map(f => f.id || f.name || '').filter(Boolean),
                };
            });
        }""")

        forms = []
        for fd in forms_data or []:
            # Find PageElements that belong to this form
            field_ids = set(fd.get("field_ids", []))
            fields = [e for e in all_elements if any(fid in e.selector for fid in field_ids)]
            submit_btn = next((e for e in all_elements if e.type == "submit"), None)
            forms.append(
                FormInfo(
                    selector=fd["selector"],
                    action=fd.get("action", ""),
                    method=fd.get("method", "get"),
                    fields=fields,
                    submit=submit_btn,
                )
            )
        return forms
    except Exception as e:
        log.warning("form_extraction_error", error=str(e))
        return []


# ── Crawler Agent ──────────────────────────────────────────────────────────────
class CrawlerAgent:
    """
    Visits a URL with a real Playwright browser and extracts
    all interactive elements with stable selectors.

    Usage:
        agent  = CrawlerAgent()
        result = agent.run("https://www.saucedemo.com")
        result = agent.run("https://app.com/dashboard",
                           auth_state="./auth_state.json")
    """

    def run(
        self,
        url: str,
        auth_state: str | None = None,
    ) -> CrawlResult:
        """
        Crawl a URL and return structured element map.

        Args:
            url:        The URL to crawl
            auth_state: Path to Playwright auth state JSON (for protected pages)

        Returns:
            CrawlResult with elements, forms, and page metadata
        """
        log.info("crawler_start", url=url, headless=settings.CRAWLER_HEADLESS)
        start_time = time.time()

        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                # Launch browser
                browser = p.chromium.launch(
                    headless=settings.CRAWLER_HEADLESS,
                )

                # Create context — with or without auth state
                context_args: dict = {
                    "viewport": {"width": 1280, "height": 720},
                }
                if auth_state:
                    context_args["storage_state"] = auth_state
                    log.info("crawler_using_auth_state", path=auth_state)

                context = browser.new_context(**context_args)
                page = context.new_page()

                # Navigate
                try:
                    page.goto(
                        url,
                        wait_until="networkidle",
                        timeout=settings.CRAWLER_TIMEOUT_MS,
                    )
                except Exception:
                    # Fallback — domcontentloaded is less strict
                    page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=settings.CRAWLER_TIMEOUT_MS,
                    )

                load_time_ms = int((time.time() - start_time) * 1000)

                # Detect framework
                is_spa, framework = detect_framework(page)
                log.info("framework_detected", url=url, is_spa=is_spa, framework=framework)

                # Extract raw elements from DOM
                raw_elements = extract_elements(page)
                log.info("raw_elements_found", count=len(raw_elements))

                # Detect navigation triggers
                nav_indices = detect_navigation_triggers(page, raw_elements)

                # Convert to PageElement with best selector
                page_elements: list[PageElement] = []
                for i, raw in enumerate(raw_elements):
                    if len(page_elements) >= settings.CRAWLER_MAX_ELEMENTS:
                        break

                    selector, strategy = get_best_selector(raw)

                    # Get label for input elements
                    label = ""
                    if raw["tag"] == "input" and raw.get("id"):
                        label = extract_label_for_input(page, f"#{raw['id']}")

                    el = PageElement(
                        tag=raw["tag"],
                        selector=selector,
                        type=raw.get("type", ""),
                        name=raw.get("name", ""),
                        placeholder=raw.get("placeholder", ""),
                        label=label or raw.get("placeholder", ""),
                        text=raw.get("text", ""),
                        role=raw.get("role", ""),
                        is_visible=raw.get("is_visible", True),
                        is_enabled=raw.get("is_enabled", True),
                        triggers_navigation=i in nav_indices,
                        attributes=raw.get("attributes", {}),
                        selector_strategy=strategy,
                    )
                    page_elements.append(el)

                # Detect login form
                has_auth = any(e.type == "password" for e in page_elements)

                # Page metadata
                metadata = PageMetadata(
                    url=url,
                    title=page.title(),
                    is_spa=is_spa,
                    framework=framework,
                    load_time_ms=load_time_ms,
                    has_auth=has_auth,
                )

                # Extract forms
                forms = extract_forms(page, page_elements)

                browser.close()

                result = CrawlResult(
                    url=url,
                    metadata=metadata,
                    elements=page_elements,
                    forms=forms,
                    warnings=[],
                    success=True,
                )

                log.info(
                    "crawler_complete",
                    url=url,
                    elements=len(page_elements),
                    forms=len(forms),
                    is_spa=is_spa,
                    load_ms=load_time_ms,
                )

                return result

        except Exception as e:
            log.error("crawler_error", url=url, error=str(e))
            return CrawlResult(
                url=url,
                metadata=PageMetadata(url=url),
                elements=[],
                forms=[],
                warnings=[f"Crawler failed: {e}"],
                success=False,
            )
