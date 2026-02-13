# Contributing to Magpie

## Issues

### Labels

**Category:**
| Label | Use for |
|-------|---------|
| `enhancement` | New features |
| `bug` | Broken functionality |
| `tech-debt` | Refactoring, cleanup |
| `testing` | Test coverage |
| `docs` | Documentation |
| `installer` | Install script |
| `deployment` | Generic deployment features (not environment-specific) |
| `dependencies` | Automated dependency updates (Dependabot) |
| `security` | Security-related issues and improvements |

**Priority:**
| Label | Meaning |
|-------|---------|
| `priority:high` | Do soon |
| `priority:medium` | Normal |
| `priority:low` | Nice to have |

### Milestones

- **vX.Y.Z** - Targeted for that release
- **No milestone** - Backlog (no timeline)

### Triage Guidelines

- Environment-specific deployment tasks belong in deployment/infra-deployment repos
- Generic deployment *features* (installer options, etc.) stay here
- Use `blocked` label + comment explaining the dependency

## Pull Requests

- Branch from `default`, target `default`
- CI must pass (lint, test, security, e2e)
- Squash merge preferred for clean history
- **API changes**: Review [API Compatibility Policy](docs/api-compatibility.md) to ensure backward compatibility

## Commits

Include co-author line for AI-assisted work:
```
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
```
