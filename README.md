# test-forge 🔧

> AI-powered test script generator — upload manual test cases, get production-ready Playwright scripts.

test-forge reads your existing manual test cases (Excel, CSV, Word, PDF, Markdown), crawls your web application to extract real DOM selectors, and generates runnable Playwright Python test scripts using a multi-agent AI pipeline.

---

## What problem does it solve?

Manual testers write test cases in spreadsheets or documents. Converting those into automation scripts is slow, repetitive, and requires Playwright expertise. test-forge eliminates that conversion step.

```
Before:  Manual test case → Developer spends hours → Playwright script
After:   Manual test case → test-forge → Playwright script (minutes)
```

---

## Pipeline overview

```
┌─────────────────────────────────────────────────────────────┐
│                        User Input                           │
│          Test case file (CSV/Excel/Word/PDF/MD)             │
│          + Target application URL                           │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    1. Reader Agent                          │
│   Parses any file format → list of structured TestCase      │
│   objects with id, steps, expected result, URL              │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    2. Planner Agent                         │
│   LLM analyses each test case → identifies which URLs       │
│   the test needs to visit → HTTP validates each URL         │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    3. Auth Agent                            │
│   Opens real browser → logs into the app → saves           │
│   session (cookies + localStorage) for reuse               │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    4. Crawler Agent                         │
│   Visits each planned URL with the saved session →         │
│   extracts real DOM selectors (data-test, id, aria, css)   │
│   Merges elements from all pages per test case             │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   5. Generator Agent                        │
│   LLM maps test steps to real selectors → produces         │
│   structured action lines → template renders full          │
│   Playwright Python file per test case                     │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   6. Validator Agent                        │
│   Level 1: Syntax check (py_compile)                       │
│   Level 2: Structure check (AST — test function, imports)  │
│   Level 3: Content check (no LLM artefacts, bad selectors) │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                      Outputs                                │
│   outputs/playwright/test_tc001_login.py                   │
│   outputs/playwright/test_tc002_locked_user.py             │
│   outputs/playwright/conftest.py                           │
└─────────────────────────────────────────────────────────────┘
```

---

## Project structure

```
test-forge/
├── src/test_forge/
│   ├── agents/
│   │   ├── reader_agent.py      ← parses any test case file format
│   │   ├── planner_agent.py     ← LLM identifies URLs each test needs
│   │   ├── auth_agent.py        ← logs in, saves browser session
│   │   ├── crawler_agent.py     ← extracts real DOM selectors
│   │   ├── generator_agent.py   ← LLM generates Playwright code
│   │   └── validator_agent.py   ← 3-level validation of generated scripts
│   ├── frameworks/
│   │   ├── base_template.py     ← abstract template interface
│   │   └── playwright_template.py ← Playwright-specific code generation
│   ├── api/
│   │   ├── server.py            ← FastAPI endpoints
│   │   ├── jobs.py              ← background job store and runner
│   │   └── models.py            ← Pydantic request/response schemas
│   ├── ui/
│   │   └── app.py               ← Streamlit web interface
│   ├── core/
│   │   ├── models.py            ← shared Pydantic data models
│   │   └── llm.py               ← LLM factory (OpenAI / Anthropic / Bedrock)
│   ├── config/
│   │   └── settings.py          ← Pydantic settings from .env
│   └── orchestrator.py          ← coordinates all agents, CLI entry point
├── tests/
│   └── unit/                    ← 195 unit tests, all agents mocked
├── inputs/                      ← drop your test case files here
├── outputs/playwright/          ← generated scripts appear here
├── pyproject.toml
├── Makefile
└── .env.example
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Package manager | uv |
| LLM providers | OpenAI / Anthropic / AWS Bedrock |
| LLM framework | LangChain |
| Browser automation | Playwright (Chromium) |
| API | FastAPI + uvicorn |
| UI | Streamlit |
| Config | Pydantic Settings |
| Logging | structlog |
| Testing | pytest + pytest-playwright |
| Linting | ruff |
| Type checking | mypy |
| CI | GitHub Actions |

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/karthikrasamsetti/test-forge
cd test-forge
make install
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
# LLM — choose one provider
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...

# Target app credentials (for Auth Agent)
DEFAULT_BASE_URL=https://www.saucedemo.com
APP_USERNAME=standard_user
APP_PASSWORD=secret_sauce

# LangSmith tracing (optional)
LANGCHAIN_TRACING_V2=true
LANGSMITH_API_KEY=ls__...
LANGSMITH_PROJECT=test-forge
```

### 3. Install Playwright browsers

```bash
playwright install chromium
```

---

## Usage

### Option 1 — Command line

```bash
make generate
# or directly:
test-forge --input inputs/sample_test_cases.csv --url https://www.saucedemo.com
```

Flags:
```
--input               Path to test case file (CSV, Excel, Word, PDF, MD)
--url                 Target application URL
--framework           playwright (default)
--output-dir          ./outputs (default)
--no-auth             Skip authentication step
--force-auth-refresh  Force fresh login even if session exists
```

### Option 2 — Web UI

Start both servers in separate terminals:

```bash
# Terminal 1 — API
make dev

# Terminal 2 — UI
make ui
```

Open `http://localhost:8501`:

1. Upload your test case file
2. Enter the target URL
3. Click **Generate**
4. Watch the pipeline run with live progress
5. View and download generated scripts
6. Click **Run Generated Tests** to execute with pytest

### Option 3 — REST API

```bash
# Submit a generation job
curl -X POST http://localhost:8000/generate \
  -F "file=@inputs/sample_test_cases.csv" \
  -F "target_url=https://www.saucedemo.com" \
  -F "force_auth_refresh=true"

# Response: {"job_id": "abc-123", "status": "pending"}

# Poll status
curl http://localhost:8000/status/abc-123

# Get results when done
curl http://localhost:8000/scripts/abc-123
```

API docs available at `http://localhost:8000/docs`

---

## Test case file format

### CSV / Excel

| Column | Required | Description |
|---|---|---|
| ID | yes | Unique test ID (e.g. TC001) |
| Description | yes | Short test name |
| Steps | yes | Numbered steps, one per line |
| Expected Result | yes | What success looks like |
| Preconditions | no | e.g. "User is logged in" |
| Priority | no | high / medium / low |
| Category | no | authentication / cart / etc. |
| URL | no | Specific page URL (overrides --url) |

**Tip:** If a test case has `URL` set, the crawler visits that specific page. If empty, it falls back to the `--url` argument. This enables multi-page apps to work correctly — login tests use the root URL, cart tests use `/inventory.html`, checkout tests use multiple pages.

### Example

```csv
ID,Description,Steps,Expected Result,Preconditions,Priority,Category,URL
TC001,Login with valid credentials,"1. Navigate to login page
2. Enter username standard_user
3. Enter password secret_sauce
4. Click Login button",User redirected to inventory page,None,high,authentication,https://www.saucedemo.com
TC004,Add product to cart,"1. Click Add to cart on Sauce Labs Backpack
2. Check cart icon","Cart shows 1 item",User is logged in,medium,cart,https://www.saucedemo.com/inventory.html
```

---

## Agent details

### Reader Agent

Supports CSV, Excel (.xlsx/.xls), Word (.docx), PDF, and Markdown. Rule-based parsing for structured files (CSV/Excel), LLM-based parsing for unstructured files (Word/PDF). Column name aliasing handles any naming variation automatically.

### Planner Agent

Makes one LLM call per test case to identify which URLs that test needs. Results are cached — repeated calls are free. HTTP HEAD validation filters out 404 URLs before crawling. The base URL is always included as a fallback.

### Auth Agent

Opens a real Chromium browser, fills credentials, submits the login form, detects success by URL change, and saves the full browser session (cookies + localStorage) to disk. Sessions are reused if less than 30 minutes old. Force refresh with `--force-auth-refresh`.

### Crawler Agent

Visits each URL using the saved session. Selector priority: `data-test` > `data-testid` > `id` > `aria-label` > `name` > `placeholder` > CSS. Auto-generated IDs are detected and skipped. SPA framework detection (React, Vue, Angular). Detects navigation-triggering elements.

### Generator Agent

Builds a structured prompt with the test steps and all available selectors from the crawl. Makes one LLM call per test case using either the standard system prompt (unauthenticated tests) or the auth-aware prompt (tests where preconditions say "user is logged in"). Renders the result through the Playwright template. Includes a quote sanitizer to fix LLM-produced selector escaping issues.

### Validator Agent

Three-level check on every generated file:
- **Level 1** — `py_compile` catches syntax errors
- **Level 2** — AST checks for test function presence, Playwright imports, non-empty body, no Selenium methods
- **Level 3** — Text scan for LLM artefacts (backticks, `# UNPARSED:`, `# UNKNOWN ACTION:`), empty selectors

Scripts that fail validation are marked as failed in the pipeline report and excluded from the output file list.

---

## Generated output example

```python
"""Generated by test-forge — do not edit manually."""

import re

import pytest
from playwright.sync_api import Page, expect

BASE_URL = "https://www.saucedemo.com"


def test_tc001_login_with_valid_credentials(page: Page) -> None:
    """TC001 — Login with valid credentials"""
    page.goto("https://www.saucedemo.com")
    page.fill("[data-test='username']", "standard_user")
    page.fill("[data-test='password']", "secret_sauce")
    page.click("[data-test='login-button']")
    page.wait_for_url("**/inventory.html")
    expect(page).to_have_url(re.compile(r"/inventory.html"))
```

Run generated scripts:

```bash
pytest outputs/playwright/ -v --no-cov
```

---

## Development commands

```bash
make install        # install all dependencies + pre-commit hooks
make test           # run unit tests with coverage
make check          # lint + typecheck + test
make lint           # ruff check + format
make generate       # run full pipeline on inputs/
make dev            # start API server
make ui             # start Streamlit UI
make clean          # remove cache files
```

---

## Running tests

```bash
# Unit tests (no browser, no LLM)
make test

# Integration tests (real browser, real SauceDemo)
pytest -m integration -v

# Run generated Playwright scripts
pytest outputs/playwright/ -v --no-cov
```

Coverage threshold: 70% minimum. Current: ~84%.

---

## Architecture decisions

**Why multi-agent?** Each agent has a single responsibility — easier to test, debug, and replace. The Crawler Agent can be swapped for a Selenium-based one without touching the Generator.

**Why LangChain?** Consistent interface across OpenAI, Anthropic, and Bedrock. Switching providers is one `.env` change.

**Why Playwright for crawling AND testing?** Same browser engine for crawling means the selectors found during crawl work reliably in the generated tests.

**Why in-memory job store?** Sufficient for MVP. Replace `JobStore` with a Redis-backed implementation for production.

**Why `data-test` attributes as first priority?** They are stable across refactors. Teams that follow this convention get near-perfect selector accuracy.

---

## Known limitations

- Generated scripts may need human review for complex multi-page flows
- Apps without semantic selectors (`data-test`, `id`, `aria-label`) produce less reliable scripts
- In-memory job store — jobs lost on server restart
- Session files stored locally — not suitable for distributed deployment
- LLM accuracy depends on test case specificity — vague steps produce vague scripts

---

## Roadmap

- [ ] HTML test reports with screenshots on failure
- [ ] Auto-fix loop — run, fail, patch selector, re-run
- [ ] Test case quality scorer — flag vague steps before generation
- [ ] Redis-backed job store for production deployment
- [ ] Docker Compose production setup
- [ ] Selenium framework template
- [ ] CI/CD integration (GitHub Actions workflow generator)

---

## License

MIT
