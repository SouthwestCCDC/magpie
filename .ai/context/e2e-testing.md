# E2E Testing

End-to-end tests validate magpie in real Docker environments.

## Running Existing E2E Tests

```bash
uv run pytest tests/e2e/ -v --tb=short
```

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
```

### Test Operations

```bash
export MAGPIE_SERVER=http://localhost:8080
export MAGPIE_TOKEN=<token from init>

# Push an artifact
uv run magpie push /tmp/test.txt artifacts/test.txt

# Get an artifact
uv run magpie get artifacts/test.txt /tmp/out.txt

# List artifacts
uv run magpie list artifacts/
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
```
relevant output
```

**Recommendations**: What to fix
```

## Key Files

- Existing E2E tests: `tests/e2e/`
- Docker config: `docker-compose.yml`
