# CIDR Allow-List E2E Tests

This document explains how to run the CIDR allow-list E2E tests and why they require special infrastructure.

## Why Special Infrastructure?

The CIDR allow-list feature (`MAGPIE_ALLOWED_CIDRS`) allows IPs in specific CIDR ranges to read artifacts without authentication. Testing this feature properly requires testing both positive AND negative cases:

1. **Positive case**: IPs INSIDE the CIDR can read without auth (200)
2. **Negative case**: IPs OUTSIDE the CIDR cannot read without auth (401)

To test both cases, we use a two-network architecture:

- **allowed-net** (172.18.0.0/24) - IPs in this network ARE in the allow-list
- **external-net** (192.168.100.0/24) - IPs in this network are NOT in the allow-list
- **test-runner-inside** - Container on allowed-net, tests positive cases
- **test-runner-outside** - Container on external-net, tests negative cases
- **Caddy** - Bridges both networks, configured with `MAGPIE_ALLOWED_CIDRS=172.18.0.0/24`

If we used a single network or ran tests from the host:
- All containers would have IPs in the same range
- We could only test one case (inside OR outside, not both)
- Tests wouldn't catch if the CIDR was too broad or too narrow

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

# 2. Get admin token
ADMIN_TOKEN=$(docker compose exec -T magpie magpie-ctl init --reset-admin-token 2>&1 | grep "^mgp_" | head -1)

# 3. Run tests from inside allowed network (positive cases)
docker compose exec -e MAGPIE_CIDR_ADMIN_TOKEN="$ADMIN_TOKEN" test-runner-inside \
  pytest tests/e2e/test_cidr_allowlist.py -k "not OutsideIP" -v

# 4. Run tests from outside allowed network (negative cases)
docker compose exec -e MAGPIE_CIDR_ADMIN_TOKEN="$ADMIN_TOKEN" test-runner-outside \
  pytest tests/e2e/test_cidr_allowlist.py::TestCIDRAllowListOutsideIPDenied -v

# 5. Cleanup
docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml down
```

## Test Organization

### `test_cidr_allowlist.py` Classes

| Class | Purpose | Security Level |
|-------|---------|----------------|
| `TestCIDRAllowListReadAccess` | Verify allowed IPs can read without auth (positive case) | CRITICAL |
| `TestCIDRAllowListWriteBlocked` | Verify allowed IPs cannot write without auth | CRITICAL |
| `TestCIDRAllowListOutsideIPDenied` | Verify outside IPs cannot read without auth (negative case) | CRITICAL |
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

This means `MAGPIE_CIDR_TEST_ENABLED` environment variable is not set. You must run tests inside a test-runner container:

```bash
docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml up -d
# For inside network tests:
docker compose exec test-runner-inside pytest tests/e2e/test_cidr_allowlist.py -v
# For outside network tests:
docker compose exec test-runner-outside pytest tests/e2e/test_cidr_allowlist.py::TestCIDRAllowListOutsideIPDenied -v
```

### Inside Network Tests Fail with 401

If tests in `TestCIDRAllowListReadAccess` fail with 401, check:
1. `MAGPIE_ALLOWED_CIDRS=172.18.0.0/24` is set in docker-compose.cidr-test.yml
2. Tests are running inside test-runner-inside container (not on host)
3. Caddy container has the environment variable (check with `docker compose exec caddy env | grep CIDR`)
4. test-runner-inside is on the allowed-net network

### Outside Network Tests Pass with 200 (Should Fail with 401)

If tests in `TestCIDRAllowListOutsideIPDenied` pass when they should fail, this indicates a SECURITY ISSUE:
1. Check that CIDR is not too broad (should be 172.18.0.0/24, not 172.16.0.0/12)
2. Verify test-runner-outside is on external-net (192.168.100.x)
3. Check Caddy logs to see what IP it's seeing

### "Connection refused" Errors

The test-runner container may be trying to connect before services are ready. Wait a few seconds and retry, or add health checks to docker-compose.cidr-test.yml.

## Network Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│ allowed-net (172.18.0.0/24) - IN allow-list             │
│                                                           │
│  ┌──────────────────┐         ┌──────────────────┐      │
│  │ test-runner-     │────────▶│ Caddy            │      │
│  │ inside           │  HTTP   │ (bridges both    │      │
│  │ (172.18.0.x)     │         │  networks)       │      │
│  └──────────────────┘         │                  │      │
│        ▲                      │ CIDR allow-list: │      │
│        │                      │ 172.18.0.0/24    │      │
│        │ 200 OK (CIDR bypass) └─────────┬────────┘      │
│        │                                 │               │
│  ┌─────┴──────────┐                     │               │
│  │ magpie         │◀────────────────────┘               │
│  │ (172.18.0.x)   │ Backend requests                    │
│  └────────────────┘                                     │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ external-net (192.168.100.0/24) - NOT in allow-list     │
│                                                           │
│  ┌──────────────────┐         ┌──────────────────┐      │
│  │ test-runner-     │────────▶│ Caddy            │      │
│  │ outside          │  HTTP   │ (bridges both    │      │
│  │ (192.168.100.x)  │         │  networks)       │      │
│  └──────────────────┘         └──────────────────┘      │
│        │                                                 │
│        └─ 401 Unauthorized (CIDR bypass denied)         │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

## Related Files

- `tests/e2e/conftest.py` - CIDR testing fixtures (cidr_http_client, cidr_outside_http_client)
- `docker-compose.cidr-test.yml` - Two-network CIDR test environment configuration
- `Caddyfile` - CIDR bypass implementation
- `scripts/run-cidr-tests.sh` - Helper script that runs tests from both containers
- Issue #371 - Original issue for these tests
- PR #375 - Previous attempt (tests removed due to infrastructure issues)
- PR #386 - Current implementation with two-network architecture
