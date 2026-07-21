# E2E Testing

End-to-end tests validate magpie in real Docker environments.

## Running Existing E2E Tests

```bash
just e2e
```

Note: E2E tests automatically start/stop their own Docker Compose stack with isolated project names (`tests/e2e/conftest.py` manages the lifecycle). You only need Docker and Docker Compose installed; do not run `docker compose up` yourself before `just e2e`. The manual commands below are for ad-hoc testing only.

## Manual Testing with Docker Compose

### Start the Stack

```bash
docker compose up -d --build
docker compose ps  # Verify all services running
```

### Initialize and Get Token

```bash
docker compose exec magpie magpie-ctl init
# Note the token output

# If re-initializing after previous runs, use:
# docker compose exec magpie magpie-ctl init --reset-admin-token
# Or completely reset: docker compose down -v && docker compose up -d --build
```

### Test Operations

```bash
export MAGPIE_SERVER=http://localhost:8080
export MAGPIE_TOKEN=<token from init>

# Push an artifact
uv run magpie push /tmp/test.txt --to artifacts/test.txt

# Get an artifact
uv run magpie get artifacts/test.txt -o /tmp/out.txt

# List artifacts
uv run magpie ls artifacts/
```

### Cleanup

```bash
docker compose down
```

## Test Output Format

```markdown
## E2E Test Results

**Environment**: Docker Compose, Python {version}

| Feature | Status | Notes |
|---------|--------|-------|
| Push | PASS | |
| Get | PASS | |
| Auth | FAIL | Token validation error |

**Issues Found**:
1. Description of failures

**Logs**:
    relevant output

**Recommendations**: What to fix
```

## Key Files

- Existing E2E tests: `tests/e2e/`
- Docker config: `docker-compose.yml`

## Release-Gate Upgrade Test

`scripts/test_upgrade_561b.sh` is a separate, heavier test: a real
v0.1.x two-container install upgraded in place to the bundled v0.2.0+
single container via each version's own installer, plus a fault-injection
run proving the backup/assert/rollback envelope actually restores a
working prior stack with data intact. It is root-required, mutates real
systemd/Docker state, and needs network access to github.com and
ghcr.io -- not part of `just e2e`/CI. Run it by hand (it no-ops with
usage instructions unless `MAGPIE_RUN_LIVE_UPGRADE_TEST=1` is set):

```bash
MAGPIE_RUN_LIVE_UPGRADE_TEST=1 scripts/test_upgrade_561b.sh
```
