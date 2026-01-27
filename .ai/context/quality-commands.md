# Quality Commands

Quality checks use `just` recipes for consistency with CI.

## Commands

| Command | Purpose |
|---------|---------|
| `just lint` | Lint Python code with ruff |
| `just fmt` | Format Python code with ruff |
| `just fmt-check` | Check formatting without modifying files |
| `just test-unit` | Run unit tests only |
| `just test-ci` | Run tests with coverage (excludes e2e) |
| `just check` | Run lint + format check + all tests |

## Pre-Push Checklist

Before pushing any changes:

```bash
just lint
just fmt-check
just test-unit
```

All must pass before creating or updating a PR.

## CI Verification

After pushing, verify CI passes:

```bash
gh pr checks {N} --repo SouthwestCCDC/magpie
```
