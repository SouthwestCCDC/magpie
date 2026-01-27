# Commit Conventions

## Message Format

```
type(area): brief description

- Detail 1
- Detail 2

Addresses #ISSUE_NUMBER

Co-authored-by: {AI Tool/Model} <noreply@{domain}.com>
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
| `ai` | AI tooling and agent configuration |

Note: This list is non-exhaustive. Use descriptive areas that match your change scope.

## AI Disclosure

All AI-generated commits MUST include the co-author trailer:

```
Co-authored-by: {AI Tool/Model} <noreply@{domain}.com>
```

Examples: `Claude Sonnet 4.5 <noreply@anthropic.com>`, `GitHub Copilot <noreply@github.com>`.

## Examples

```
feat(cli): add verify command for artifact integrity

- Add SHA256 verification on get operations
- Display verification status in output

Addresses #129

Co-authored-by: Claude Sonnet 4.5 <noreply@anthropic.com>
```
