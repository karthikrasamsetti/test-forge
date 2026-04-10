"""test-forge Streamlit UI — upload test cases, generate Playwright scripts."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests  # type: ignore[import-untyped]
import streamlit as st

# ------------------------------------------------------------------ #
# Config                                                               #
# ------------------------------------------------------------------ #

API_BASE = "http://localhost:8000"
POLL_INTERVAL = 2  # seconds between status polls
OUTPUT_DIR = "./outputs/playwright"

st.set_page_config(
    page_title="test-forge",
    page_icon="🔧",
    layout="wide",
)

# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #


def api_post_generate(
    file_bytes: bytes,
    filename: str,
    target_url: str,
    framework: str,
    force_auth_refresh: bool,
) -> dict[str, Any]:
    """Call POST /generate and return the response JSON."""
    response = requests.post(
        f"{API_BASE}/generate",
        data={
            "target_url": target_url,
            "framework": framework,
            "force_auth_refresh": str(force_auth_refresh).lower(),
        },
        files={"file": (filename, file_bytes, "application/octet-stream")},
        timeout=30,
    )
    response.raise_for_status()
    return dict(response.json())


def api_get_status(job_id: str) -> dict[str, Any]:
    """Call GET /status/{job_id} and return the response JSON."""
    response = requests.get(f"{API_BASE}/status/{job_id}", timeout=10)
    response.raise_for_status()
    return dict(response.json())


def api_get_scripts(job_id: str) -> dict[str, Any]:
    """Call GET /scripts/{job_id} and return the response JSON."""
    response = requests.get(f"{API_BASE}/scripts/{job_id}", timeout=10)
    if response.status_code == 202:
        return {"status": "running"}
    response.raise_for_status()
    return dict(response.json())


def api_post_run(job_id: str) -> dict[str, Any]:
    """Call POST /run/{job_id} to start a test run."""
    response = requests.post(f"{API_BASE}/run/{job_id}", timeout=10)
    response.raise_for_status()
    return dict(response.json())


def api_get_run_status(run_id: str) -> dict[str, Any]:
    """Call GET /run-status/{run_id}."""
    response = requests.get(f"{API_BASE}/run-status/{run_id}", timeout=10)
    response.raise_for_status()
    return dict(response.json())


def check_api_health() -> bool:
    """Return True if the API server is reachable."""
    try:
        r = requests.get(f"{API_BASE}/health", timeout=3)
        return bool(r.status_code == 200)
    except Exception:  # noqa: BLE001
        return False


def read_script_content(filename: str) -> str:
    """Read a generated script from disk."""
    path = Path(OUTPUT_DIR) / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"# File not found: {filename}"


# ------------------------------------------------------------------ #
# UI Components                                                        #
# ------------------------------------------------------------------ #


def render_header() -> None:
    st.title("🔧 test-forge")
    st.caption(
        "AI-powered test script generator — upload manual test cases, get Playwright scripts."
    )
    st.divider()


def render_api_status() -> bool:
    """Show API connection status. Returns True if connected."""
    healthy = check_api_health()
    if healthy:
        st.sidebar.success("✓ API connected", icon="🟢")
    else:
        st.sidebar.error("✗ API not reachable — start the server with `make dev`", icon="🔴")
    return healthy


def render_sidebar() -> tuple[str, str, bool]:
    """Render sidebar config. Returns (target_url, framework, force_refresh)."""
    st.sidebar.header("⚙️ Configuration")

    target_url = st.sidebar.text_input(
        "Target URL",
        value="https://www.saucedemo.com",
        help="URL of the web application to crawl for selectors",
    )

    framework = st.sidebar.selectbox(
        "Framework",
        options=["playwright"],
        help="Test framework for generated scripts",
    )

    force_refresh = st.sidebar.checkbox(
        "Force auth refresh",
        value=False,
        help="Force re-login even if a valid session exists",
    )

    st.sidebar.divider()
    st.sidebar.caption("Output directory: `outputs/playwright/`")

    return target_url, str(framework), force_refresh


def render_upload_section() -> tuple[bytes | None, str | None]:
    """Render file upload section. Returns (file_bytes, filename)."""
    st.subheader("📁 Upload Test Cases")

    uploaded = st.file_uploader(
        "Choose a test case file",
        type=["csv", "xlsx", "xls", "docx", "pdf", "md"],
        help="Supported formats: CSV, Excel, Word, PDF, Markdown",
    )

    if uploaded:
        st.success(f"✓ File ready: **{uploaded.name}** ({len(uploaded.getvalue()):,} bytes)")
        return uploaded.getvalue(), uploaded.name

    return None, None


def render_progress(job_id: str) -> dict:
    """Poll job status and show progress. Returns final status dict."""
    status_placeholder = st.empty()
    progress_bar = st.progress(0)
    log_placeholder = st.empty()

    # Rough progress simulation based on log messages
    running_steps = [
        (15, "🔐 Authenticating..."),
        (30, "🕷️ Crawling pages..."),
        (50, "🤖 Planning URLs..."),
        (70, "⚙️ Generating scripts..."),
        (90, "✅ Validating output..."),
    ]
    step_idx = 0
    elapsed = 0

    while True:
        try:
            status = api_get_status(job_id)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Failed to poll status: {exc}")
            break

        current_status = status.get("status", "pending")

        if current_status == "done":
            progress_bar.progress(100)
            status_placeholder.success("✓ Generation complete!")
            log_placeholder.empty()
            return status

        if current_status == "failed":
            progress_bar.progress(100)
            status_placeholder.error(f"✗ Generation failed: {status.get('error', 'Unknown error')}")
            log_placeholder.empty()
            return status

        # Advance simulated progress
        if step_idx < len(running_steps) and elapsed >= step_idx * 12:
            pct, msg = running_steps[step_idx]
            progress_bar.progress(pct)
            log_placeholder.info(msg)
            step_idx += 1
        elif step_idx >= len(running_steps):
            progress_bar.progress(90)

        status_placeholder.info(f"⏳ Running... ({elapsed}s elapsed)")
        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

    return {}


def render_results(job_id: str) -> None:
    """Fetch and display the generation results."""
    try:
        result = api_get_scripts(job_id)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to fetch results: {exc}")
        return

    st.divider()
    st.subheader("📊 Results")

    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Test Cases", result.get("total_test_cases", "—"))
    col2.metric("Generated", len([s for s in result.get("scripts", []) if s["success"]]))
    col3.metric("Failed", len([s for s in result.get("scripts", []) if not s["success"]]))
    col4.metric("Elapsed", f"{result.get('elapsed_seconds', 0):.1f}s")

    # Warnings
    warnings = result.get("warnings", [])
    if warnings:
        with st.expander(f"⚠️ Warnings ({len(warnings)})", expanded=False):
            for w in warnings:
                st.warning(w)

    # Generated scripts
    scripts = result.get("scripts", [])
    if not scripts:
        st.info("No scripts were generated.")
        return

    st.subheader("📝 Generated Scripts")

    for script in scripts:
        filename = script["filename"]
        success = script["success"]
        icon = "✅" if success else "❌"

        with st.expander(f"{icon} {filename}", expanded=False):
            if script.get("warnings"):
                for w in script["warnings"]:
                    st.warning(w)

            content = read_script_content(filename)
            st.code(content, language="python")

            st.download_button(
                label=f"⬇️ Download {filename}",
                data=content,
                file_name=filename,
                mime="text/x-python",
                key=f"download_{filename}",
            )

    # Download all as zip
    output_files = result.get("output_files", [])
    if output_files:
        st.divider()
        st.caption(f"Scripts saved to `outputs/playwright/` — {len(output_files)} files")


# ------------------------------------------------------------------ #
# Main app                                                             #
# ------------------------------------------------------------------ #


def render_test_results(job_id: str) -> None:
    """Run tests and display results."""
    st.subheader("🧪 Running Tests")

    try:
        run = api_post_run(job_id)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to start test run: {exc}")
        return

    run_id = run["run_id"]
    status_placeholder = st.empty()
    progress_bar = st.progress(0)

    # Poll until done
    elapsed = 0
    while True:
        try:
            status = api_get_run_status(run_id)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Failed to poll run status: {exc}")
            break

        current = status.get("status", "pending")

        if current in ("done", "failed"):
            progress_bar.progress(100)
            break

        progress_bar.progress(min(elapsed * 2, 90))
        status_placeholder.info(f"⏳ Running tests... ({elapsed}s)")
        time.sleep(2)
        elapsed += 2

    status_placeholder.empty()
    progress_bar.empty()

    # Show results
    results = status.get("results", [])
    total = status.get("total", 0)
    passed = status.get("passed", 0)
    failed = status.get("failed", 0)
    elapsed_s = status.get("elapsed_seconds", 0)

    # Summary
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total", total)
    col2.metric("Passed", passed, delta=None)
    col3.metric("Failed", failed, delta=None)
    col4.metric("Duration", f"{elapsed_s:.1f}s")

    if failed == 0 and total > 0:
        st.success(f"✅ All {total} tests passed!")
    elif failed > 0:
        st.error(f"❌ {failed}/{total} tests failed")

    # Per-test results
    if results:
        st.divider()
        for r in results:
            icon = "✅" if r["passed"] else "❌"
            color = "green" if r["passed"] else "red"
            st.markdown(
                f"{icon} `{r['filename']}` — **:{color}[{'PASSED' if r['passed'] else 'FAILED'}]**"
            )

    # Raw output
    stdout = status.get("stdout", "")
    if stdout:
        with st.expander("📋 Full pytest output", expanded=failed > 0):
            st.code(stdout, language="text")


def main() -> None:
    render_header()

    # Sidebar
    api_ok = render_api_status()
    target_url, framework, force_refresh = render_sidebar()

    # Main content
    file_bytes, filename = render_upload_section()

    st.divider()

    # Generate button
    col1, col2 = st.columns([1, 4])
    generate_clicked = col1.button(
        "🚀 Generate",
        disabled=not api_ok or file_bytes is None,
        type="primary",
        use_container_width=True,
    )

    if not api_ok:
        st.warning("Start the API server first: `make dev`")
        return

    if file_bytes is None:
        st.info("Upload a test case file to get started.")
        return

    if generate_clicked:
        st.subheader("⏳ Running Pipeline")

        # Submit job
        try:
            job = api_post_generate(
                file_bytes=file_bytes,
                filename=filename or "upload.csv",
                target_url=target_url,
                framework=framework,
                force_auth_refresh=force_refresh,
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Failed to start job: {exc}")
            return

        job_id = job["job_id"]
        st.caption(f"Job ID: `{job_id}`")

        # Poll until done
        final_status = render_progress(job_id)

        # Show results
        if final_status.get("status") == "done":
            render_results(job_id)

            # Run Tests button
            st.divider()
            if st.button("🧪 Run Generated Tests", type="secondary", use_container_width=False):
                render_test_results(job_id)

        elif final_status.get("status") == "failed":
            st.error(final_status.get("error", "Unknown error"))


if __name__ == "__main__":
    main()
