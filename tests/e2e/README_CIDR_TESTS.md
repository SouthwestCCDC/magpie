# CIDR Allow-List E2E Tests

## Overview

The CIDR allow-list feature (`MAGPIE_ALLOWED_CIDRS`) allows IPs in specific CIDR ranges to read artifacts without authentication. These tests verify both positive cases (allowed IPs CAN read without auth) and negative cases (disallowed IPs CANNOT read without auth).

Testing requires a two-network Docker Compose setup:
- `allowed-net` (172.18.0.0/24) - IPs in allow-list
- `external-net` (192.168.100.0/24) - IPs NOT in allow-list
- Two test-runner containers, one on each network

See `docker-compose.cidr-test.yml` for the network configuration.

## Running Tests

Use the helper script (recommended):

```bash
./scripts/run-cidr-tests.sh          # Run all CIDR tests
./scripts/run-cidr-tests.sh -vv      # Very verbose output
```

The script handles Docker Compose setup, runs tests from both networks, and cleans up afterward.

## Troubleshooting

**Tests skip with "CIDR tests require running inside test-runner container"**

The `MAGPIE_CIDR_TEST_ENABLED` environment variable is not set. Use the script above or run tests inside the test-runner containers manually.

**Inside network tests fail with 401**

Check that `MAGPIE_ALLOWED_CIDRS=172.18.0.0/24` is set in `docker-compose.cidr-test.yml` and that tests are running inside the `test-runner-inside` container.

**Outside network tests pass with 200 (should fail with 401)**

This is a SECURITY ISSUE. Verify the CIDR range is not too broad and that `test-runner-outside` is on `external-net` (192.168.100.x).
