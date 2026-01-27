# Quality Commands

Quality checks use `uv run` for consistent Python environment.

## Commands

| Command | Purpose |
|---------|---------|
| `uv run ruff check src/ tests/` | Lint Python code |
| `uv run ruff format src/ tests/` | Format Python code |
| `uv run pytest tests/unit/ -x` | Run unit tests (stop on first failure) |
| `uv run pytest tests/e2e/ -v` | Run E2E tests |
| `uv run pytest tests/ -v` | Run all tests |

## Pre-Push Checklist

Before pushing any changes:

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run pytest tests/unit/ -x
```

All must pass before creating or updating a PR.

## CI Verification

After pushing, verify CI passes:

```bash
gh pr checks {N} --repo SouthwestCCDC/magpie
```
