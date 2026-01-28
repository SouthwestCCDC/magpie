# CIDR Allow-List E2E Tests

This document explains how to run the CIDR allow-list E2E tests and why they require special infrastructure.

## Why Special Infrastructure?

The CIDR allow-list feature (`MAGPIE_ALLOWED_CIDRS`) allows IPs in specific CIDR ranges to read artifacts without authentication. Testing this feature requires:

1. **Running tests from within the Docker network** - The test client must have an IP in the allowed CIDR range (172.16.0.0/12)
2. **Separate Docker Compose configuration** - We need to set `MAGPIE_ALLOWED_CIDRS` environment variable
3. **Test-runner container** - A container that runs pytest from inside the Docker network

If tests run from the host machine:
- Host IP appears different to Caddy (not in 172.16.0.0/12)
- CIDR bypass never triggers
- Tests fail with 401 even though the feature works correctly

## Running CIDR Tests

### Option 1: Using the Helper Script (Recommended)

```bash
# Run all CIDR tests
./scripts/run-cidr-tests.sh

# Run with verbose output
./scripts/run-cidr-tests.sh -v

# Run specific test
./scripts/run-cidr-tests.sh -k test_allowed_ip_can_list
```

The script:
1. Starts Docker Compose with CIDR testing enabled
2. Runs tests inside the test-runner container
3. Cleans up containers and volumes

### Option 2: Manual Steps

```bash
# 1. Start the CIDR test environment
docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml up -d --build

# 2. Run tests inside test-runner container
docker compose exec test-runner pytest tests/e2e/test_cidr_allowlist.py -v

# 3. Cleanup
docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml down
```

## Test Organization

### `test_cidr_allowlist.py` Classes

| Class | Purpose | Security Level |
|-------|---------|----------------|
| `TestCIDRAllowListReadAccess` | Verify allowed IPs can read without auth | CRITICAL |
| `TestCIDRAllowListWriteBlocked` | Verify allowed IPs cannot write without auth | CRITICAL |
| `TestCIDRAllowListPublicPaths` | Verify public paths work regardless of CIDR | Medium |

### Test Coverage Matrix

| Operation | Endpoint | Expected Behavior |
|-----------|----------|-------------------|
| List artifacts | `GET /api/v1/artifacts/` | 200 (CIDR bypass) |
| Get metadata | `GET /api/v1/artifacts/{path}` | 200 (CIDR bypass) |
| Get info | `GET /api/v1/artifacts/{path}/{ref}/info` | 200 (CIDR bypass) |
| Download | `GET /artifacts/*` | 200 (CIDR bypass) |
| Upload | `POST /api/v1/upload/*` | 401 (auth required) |
| Amend metadata | `PATCH /api/v1/artifacts/*` | 401 (auth required) |
| Add tag | `POST /api/v1/artifacts/{path}/{ref}/tags` | 401 (auth required) |
| Delete tag | `DELETE /api/v1/artifacts/{path}/tags/{tag}` | 401 (auth required) |
| Flush tag | `POST /api/v1/tags/{tag}/flush` | 401 (auth required) |
| List tokens | `GET /api/v1/tokens` | 401 (auth required) |
| Create token | `POST /api/v1/tokens` | 401 (auth required) |
| Run GC | `POST /api/v1/gc` | 401 (auth required) |
| Get status | `GET /api/v1/status` | 401 (auth required) |

## CI Integration

These tests are not included in the default `just e2e` command because they require special Docker Compose configuration. They should be run separately in CI:

```yaml
# .github/workflows/e2e.yml
- name: Run CIDR E2E tests
  run: ./scripts/run-cidr-tests.sh
```

## Troubleshooting

### Tests Skip with "CIDR tests require running inside test-runner container"

This means `MAGPIE_CIDR_TEST_ENABLED` environment variable is not set. You must run tests inside the test-runner container:

```bash
docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml up -d
docker compose exec test-runner pytest tests/e2e/test_cidr_allowlist.py -v
```

### All Tests Fail with 401

Check that:
1. `MAGPIE_ALLOWED_CIDRS=172.16.0.0/12` is set in docker-compose.cidr-test.yml
2. Tests are running inside test-runner container (not on host)
3. Caddy container has the environment variable (check with `docker compose exec caddy env | grep CIDR`)

### "Connection refused" Errors

The test-runner container may be trying to connect before services are ready. Wait a few seconds and retry, or add health checks to docker-compose.cidr-test.yml.

## Related Files

- `tests/e2e/conftest.py` - CIDR testing fixtures
- `docker-compose.cidr-test.yml` - CIDR test environment configuration
- `Caddyfile` - CIDR bypass implementation (lines 96-172)
- Issue #371 - Original issue for these tests
- PR #375 - Previous attempt (tests removed due to infrastructure issues)
