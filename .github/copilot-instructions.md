# Magpie - AI Assistant Context

This document provides context for AI assistants working on the Magpie project.

## Project Overview

**Magpie** is a versioned artifact storage system with content-addressing and mutable tags, replacing the current S3-synced file server used by SWCCDC.

- **Repository:** `SouthwestCCDC/magpie`
- **Status:** Implementation complete, entering deployment and operations phase

## Key Documentation

For full context, read these documents in `deployment/docs/docs/projects/active/magpie/`:

| Document | Description |
|----------|-------------|
| `design.md` | **Primary reference** - detailed design, architecture, all resolved decisions |
| `proposal.md` | One-pager summary with progress checklist |
| `index.md` | Project overview |

Also in the repo root:

| Document | Description |
|----------|-------------|
| `CONTRIBUTING.md` | Labels, milestones, triage guidelines, PR workflow |

## Architecture Summary

- **Caddy** serves static files directly from filesystem
- **FastAPI service** handles uploads, tag management, auth validation
- **Filesystem** is source of truth (no database for artifacts)
- **SQLite** stores tokens only
- **CLI tools:** `magpie` (client), `magpie-ctl` (server admin)

## Technology Stack

| Component | Choice |
|-----------|--------|
| Python | 3.13+ |
| Package manager | uv |
| Web framework | FastAPI |
| CLI | Click |
| Linting | ruff (lint + format), bandit |
| File server | Caddy |

## Project Structure

```
src/magpie/
  __init__.py      # Package root, version
  server/          # FastAPI application
    app.py         # Main app with /health endpoint
    routes/        # API endpoints (to be implemented)
  storage/         # Filesystem operations (to be implemented)
  cli/             # Client CLI (magpie command)
  ctl/             # Server admin CLI (magpie-ctl command)
```

<!-- ## Implementation Phases

### Phase 1: Core Upload + Filesystem (complete)
- [x] Scaffolding (pyproject.toml, Dockerfile, docker-compose, FastAPI, CLIs, tooling)
- [x] Storage operations (compute_hash, store_artifact, create_tag, list_artifacts)
- [x] Upload endpoint (stream, hash, dedupe, move, metadata, symlink)

### Phase 2: Tags + CLI (complete)
- [x] Tag endpoints (POST/DELETE)
- [x] Full CLI implementation (config, push, get, ls, info, tag, untag, url, amend, gc, flush-tag)
- [x] Token auth (forward_auth, SQLite storage)

### Phase 3: Polish + Deployment (complete)
- [x] amend command/endpoint
- [x] gc with symlink reconciliation
- [x] flush-tag with confirmation
- [x] Observability (Sentry SDK, OpenTelemetry)
- [x] Production Caddy configuration with TLS

### Phase 4: S3 Backup (future)

-->

## Key Design Decisions

These are documented in detail in `design.md` Resolved Design Questions table:

- **Hash prefix:** 8 characters (SHA-256)
- **Config hierarchy:** file < env < CLI arg
- **CLI config:** `~/.magpie/config.toml`
- **Server config:** Pydantic Settings with `MAGPIE_` prefix
- **Duplicate uploads:** Return existing hash, update `latest`, warn about `--amend`
- **Forward auth:** `GET /api/v1/auth/validate`, returns 200/401
- **Token management:** Both `magpie-ctl token` CLI and REST API

## Commands

```bash
# Development
uv sync                                    # Install dependencies
uv run uvicorn magpie.server.app:app --reload  # Run server
uv run magpie --help                       # Client CLI
uv run magpie-ctl --help                   # Server admin CLI
docker compose up --build                  # Full stack (Caddy + service)

# Linting
uv run ruff check .                        # Lint
uv run ruff format .                       # Format
```

## Related Repositories

- `deployment` - Documentation site (design docs in `docs/docs/projects/active/magpie/`)
- `scoring` - Reference for code style alignment (ruff, line-length 100)
- `infra-deployment` - Current artifacts v1 Ansible role

## Style Guide

- Line length: 100
- Formatting: ruff format (black-compatible)
- Imports: ruff with isort rules
- No emojis in code/docs unless requested
- Align with `scoring/` repo conventions

## Code Review Guidelines

When reviewing pull requests, follow these guidelines to provide consistent, actionable feedback.

### Priority System

Categorize findings by severity:

- **CRITICAL** (blocks merge): Security vulnerabilities (auth bypass, token exposure, path traversal), data loss risks, breaking API changes
- **IMPORTANT** (should fix before merge): Missing error handling, inadequate test coverage, violation of project conventions
- **SUGGESTION** (non-blocking): Code style improvements, refactoring opportunities, documentation enhancements

### Confidence Threshold

Only comment when HIGH CONFIDENCE (>80%) an issue exists. Avoid speculative concerns without concrete evidence.

### What CI Already Checks

Our CI pipeline handles:
- Code formatting (`ruff format`)
- Linting (`ruff check`)
- Security scanning (`bandit`)
- Unit, integration, and E2E tests

**Do not duplicate feedback** on issues these tools catch. Focus on logic, architecture, and security concerns that require human judgment.

### Security Focus

Flag these with **CRITICAL** priority:
- Token handling vulnerabilities (exposure, weak generation)
- Path traversal in file operations (artifacts stored by hash)
- Authentication bypass in `forward_auth` flow
- Missing input validation on upload endpoints

### Project-Specific Review Points

- **Filesystem is source of truth**: No database for artifacts - verify file operations are correct
- **CLI vs server separation**: Client CLI should not import server internals
- **Config hierarchy**: file < env < CLI arg - verify precedence is respected
- **Hash-based storage**: Artifacts identified by SHA-256 hash prefix (8 chars)

### Review Self-Assessment

At the end of your review, include a brief summary comment with:

1. **Scope note**: If this PR has >10 changed files or >400 lines changed, note: "This is a large PR. Consider requesting a second review pass after addressing these comments."

2. **Tooling gaps**: If you flag issues that a linter could catch automatically (formatting, import order, type errors), note which tool would help rather than commenting on each instance. Examples:
   - Formatting issues → "Run `ruff format`"
   - Type errors → "Consider adding stricter `mypy` rules"
   - Security patterns → "Covered by `bandit` in CI"

3. **Files skipped**: If you skipped any files as "low risk" or due to size limits, list them so the author knows to check them manually.

4. **Categories reviewed**: Briefly note which categories you checked (security, API design, tests, storage logic) so authors know what wasn't covered if you focused narrowly.

## MANDATORY: Be transparent about AI use

Disclose when AI generates content that humans will read and might attribute to a specific person.

**What requires disclosure:**

- GitHub issues and PR descriptions
- GitHub comments (on issues, PRs, or commits)
- Documentation files (markdown, mkdocs, READMEs, guides)
- Commit messages with substantive explanations
- Technical writing (runbooks, ADRs, design docs)
- Narrative comments in code (extensive docstrings, module-level documentation blocks)

**How to disclose:**

- Match the format to the context:
  - Commits: `Co-Authored-By:` line with AI identity
  - Documentation: admonition block or footer note
  - Comments/issues: brief closing sentence
- Include tool and model when known (e.g., "Copilot w/ GPT-4.5", "Claude Code w/ Opus 4.5")

**Exceptions (no disclosure needed):**

- Mechanical operations: git merge/rebase/stash, conflict resolution, simple summary commit messages
- Human-driven content: AI only reformatted, quoted, or arranged what the user wrote
- Source code and config: code, configuration files, scripts, brief inline comments
- Trivial changes: typo fixes, formatting, single-line edits
