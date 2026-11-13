# Magpie justfile - Common development tasks

# Default recipe lists all available commands
default:
    @just --list

# Run all tests
test:
    uv run pytest

# Run tests with coverage report
test-cov:
    uv run pytest --cov=magpie --cov-report=term-missing --cov-report=html

# Run tests with CI-style coverage (term + xml output), excluding e2e tests
test-ci:
    uv run pytest -m "not e2e" --cov=src/magpie --cov-report=term --cov-report=xml

# Run only unit tests
test-unit:
    uv run pytest tests/unit

# Run only integration tests
test-integration:
    uv run pytest tests/integration

# Run e2e tests (tests manage their own Docker Compose stack)
e2e:
    uv run pytest -m e2e

# Run linting checks
lint:
    uv run ruff check .

# Tier 1 installer checks: bash -n + shellcheck on every tracked shell
# script (requires shellcheck on PATH)
lint-shell:
    ./scripts/lint_shell.sh

# Tier 1 installer checks: caddy validate on every tracked Caddyfile, in
# each shape the installer's generated .env can produce (requires Docker)
validate-caddy:
    ./scripts/validate_caddyfiles.sh

# Tier 2 installer check: real install + assertions + uninstall --purge.
# DESTRUCTIVE -- installs global systemd units and /opt/magpie. Run on a
# throwaway VM only.
test-install-e2e:
    sudo ./scripts/test_install_e2e.sh

# Run code formatting
fmt:
    uv run ruff format .

# Check code formatting without modifying files
fmt-check:
    uv run ruff format --check .

# Run security scanning with bandit
security:
    uv run bandit -r src/magpie

# Run all checks (lint + format check + tests)
check:
    uv run ruff check .
    uv run ruff format --check .
    uv run pytest

# Run all CI checks (lint, format check, security, and tests with coverage)
ci:
    @echo "Running lint checks..."
    just lint
    @echo "Running format checks..."
    just fmt-check
    @echo "Running security scan..."
    just security
    @echo "Running tests with coverage..."
    just test-ci

# Start development server with auto-reload
serve:
    uv run uvicorn magpie.server.app:app --reload

# Start development server on specific host/port
serve-custom host="0.0.0.0" port="8000":
    uv run uvicorn magpie.server.app:app --host {{host}} --port {{port}} --reload

# Build docker image
build tag="magpie:latest":
    docker build -t {{tag}} .

# Start full stack with docker compose
up:
    docker compose up --build

# Stop docker compose stack
down:
    docker compose down

# Clean temporary files and caches
clean:
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    find . -type f -name ".coverage" -delete 2>/dev/null || true

# Install/update dependencies
sync:
    uv sync

# Run pre-commit hooks on all files
pre-commit:
    uv run pre-commit run --all-files

# Show project version
version:
    @uv run python -c "from magpie import __version__; print(__version__)"
