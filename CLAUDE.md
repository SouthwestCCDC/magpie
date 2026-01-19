# CLAUDE.md - Magpie Project

This file provides guidance to Claude Code when working on the magpie project.

## Project Overview

**Magpie** is a versioned artifact storage system with content-addressing and mutable tags. It replaces the legacy S3-synced file server used by SWCCDC.

- **Repository:** SouthwestCCDC/magpie
- **Status:** Implementation complete, in deployment/operations phase

## Quick Reference

```bash
# Development
uv sync                                    # Install dependencies
uv run uvicorn magpie.server.app:app --reload  # Run server
docker compose up --build                  # Full stack (Caddy + FastAPI)

# Testing
uv run pytest tests/unit/ -v --tb=short    # Unit tests
uv run pytest tests/integration/ -v        # Integration tests
uv run pytest tests/e2e/ -v                # E2E tests (requires docker-compose)

# Linting (run before committing)
uv run ruff check src/ tests/              # Lint
uv run ruff format src/ tests/             # Format

# Security
uv run bandit -r src/                      # Security scan
```

## Key Documentation

| Document | Purpose |
|----------|---------|
| `.github/copilot-instructions.md` | Comprehensive project context and conventions |
| `docs/design.md` | Architecture decisions and design rationale |
| `docs/user-guide.md` | User-facing CLI documentation |
| `.claude/docs/orchestrator-prompt.md` | Orchestrator coordination instructions |
| `.claude/agents/` | Specialized subagent definitions |

## Architecture

```
src/magpie/
  cli/             # Client CLI (magpie command)
  ctl/             # Server admin CLI (magpie-ctl command)
  server/          # FastAPI application
    app.py         # Main app
    routes/        # API endpoints
    deps.py        # Dependencies (auth, storage)
  storage/         # Filesystem operations
  auth/            # Token management
  config.py        # Pydantic settings
```

## Technology Stack

- Python 3.13+, uv for package management
- FastAPI + Pydantic for server
- Click for CLI
- Caddy for reverse proxy and static file serving
- SQLite for token storage only (filesystem is source of truth for artifacts)

## Testing Requirements

Before submitting PRs:
1. All tests pass: `uv run pytest tests/unit/ tests/integration/ -v`
2. Linting passes: `uv run ruff check src/ tests/`
3. Security scan clean: `uv run bandit -r src/`

## Claude Tooling

This repo has specialized Claude agents in `.claude/agents/`:

| Agent | Model | Purpose |
|-------|-------|---------|
| `magpie-developer` | sonnet | Implementation (features, bugs, PR comments) |
| `magpie-pr-reviewer` | sonnet | PR review and comment triage |
| `magpie-api-researcher` | haiku | FastAPI/Pydantic documentation lookup |
| `magpie-e2e-tester` | sonnet | Docker-compose integration testing |
| `magpie-docs-writer` | haiku | Documentation updates |

For orchestrated work, use the `/magpie-orchestrator` skill.

## AI Disclosure

All GitHub comments must include AI disclosure per workspace policy (include model when known):

```
(AI-generated via Claude Code w/ Opus 4.5)
```

Commit messages should include:
```
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
```
