# Commit Conventions

## Message Format

```
type(area): brief description

- Detail 1
- Detail 2

Addresses #ISSUE_NUMBER

Co-Authored-By: Claude {Model} <noreply@anthropic.com>
```

## Types

| Type | Use For |
|------|---------|
| `feat` | New functionality |
| `fix` | Bug fixes |
| `refactor` | Code restructuring without behavior change |
| `test` | Adding or updating tests |
| `docs` | Documentation changes |
| `chore` | Build, CI, tooling changes |

## Areas

| Area | Scope |
|------|-------|
| `api` | REST API endpoints |
| `cli` | Command-line interface |
| `storage` | Artifact storage layer |
| `auth` | Authentication |
| `config` | Configuration handling |

## AI Disclosure

All AI-generated commits MUST include the co-author trailer:

```
Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
```

Use the actual model (Sonnet 4.5, Opus 4.5, Haiku, etc.).

## Examples

```
feat(cli): add verify command for artifact integrity

- Add SHA256 verification on get operations
- Display verification status in output

Addresses #129

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
```
