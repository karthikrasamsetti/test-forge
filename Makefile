.PHONY: install dev test lint format typecheck clean run-api run-ui help

# ── Setup ─────────────────────────────────────────────────────────────────────
install:
	uv pip install -e ".[dev]"
	playwright install chromium
	pre-commit install
	@echo "✓ test-forge installed"

# ── Development ───────────────────────────────────────────────────────────────
dev:
	uvicorn src.test_forge.api.server:app --reload --port 8000

ui:
	streamlit run src/test_forge/ui/app.py

# ── Quality ───────────────────────────────────────────────────────────────────
lint:
	ruff check src/ tests/ --fix
	ruff format src/ tests/
	@echo "✓ Lint passed"

format:
	ruff format src/ tests/
	@echo "✓ Format applied"

typecheck:
	mypy src/ --ignore-missing-imports --explicit-package-bases
	@echo "✓ Types checked"

test:
	pytest tests/ -v
	@echo "✓ Tests passed"

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v

coverage:
	pytest --cov=src/test_forge --cov-report=html
	@echo "Coverage report in htmlcov/index.html"

# ── All quality checks ─────────────────────────────────────────────────────────
check: lint typecheck test
	@echo "✓ All checks passed"

# ── Docker ────────────────────────────────────────────────────────────────────
docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

# ── Generate ──────────────────────────────────────────────────────────────────
generate:
	uv run python -m test_forge.cli generate --input inputs/ --framework playwright

# ── Clean ─────────────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name htmlcov -exec rm -rf {} +
	find . -name "*.pyc" -delete
	@echo "✓ Cleaned"

# ── Help ──────────────────────────────────────────────────────────────────────
help:
	@echo "test-forge commands:"
	@echo "  make install     — install all dependencies"
	@echo "  make dev         — start API server with hot reload"
	@echo "  make ui          — start Streamlit UI"
	@echo "  make test        — run all tests"
	@echo "  make lint        — run linter"
	@echo "  make format      — format code"
	@echo "  make typecheck   — run mypy"
	@echo "  make check       — run all quality checks"
	@echo "  make generate    — generate scripts from inputs/"
	@echo "  make docker-up   — start with Docker"
	@echo "  make clean       — remove cache files"
