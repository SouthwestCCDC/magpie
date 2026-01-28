#!/bin/bash
# Run CIDR allow-list E2E tests
#
# This script starts the Docker Compose stack with CIDR testing enabled,
# runs the CIDR tests inside the test-runner container, and cleans up.
#
# Usage:
#   ./scripts/run-cidr-tests.sh [pytest-args]
#
# Examples:
#   ./scripts/run-cidr-tests.sh                    # Run all CIDR tests
#   ./scripts/run-cidr-tests.sh -v                 # Verbose output
#   ./scripts/run-cidr-tests.sh -k test_read       # Run only read tests

set -euo pipefail

# Change to project root
cd "$(dirname "$0")/.."

# Default pytest args if none provided
PYTEST_ARGS="${@:--v --tb=short}"

echo "Starting CIDR test environment..."
docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml up -d --build

# Wait for services to be healthy
echo "Waiting for services to be healthy..."
sleep 5

# Check health endpoint
if ! curl -f http://localhost:8080/health > /dev/null 2>&1; then
    echo "ERROR: Services failed to start. Check logs:"
    docker compose logs
    docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml down
    exit 1
fi

# Initialize magpie and get admin token
echo "Initializing magpie and getting admin token..."
ADMIN_TOKEN=$(docker compose exec -T magpie magpie-ctl init --reset-admin-token 2>&1 | grep "^mgp_" | head -1)

if [ -z "$ADMIN_TOKEN" ]; then
    echo "ERROR: Failed to get admin token"
    docker compose logs magpie
    docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml down
    exit 1
fi

echo "Admin token obtained: ${ADMIN_TOKEN:0:10}..."

# Run tests from inside allowed network (positive cases)
echo ""
echo "=========================================="
echo "Running CIDR tests from INSIDE allowed network (172.18.0.x)..."
echo "These tests verify IPs in the allow-list CAN read without auth"
echo "=========================================="
docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml \
  exec -e MAGPIE_CIDR_ADMIN_TOKEN="$ADMIN_TOKEN" test-runner-inside \
  pytest tests/e2e/test_cidr_allowlist.py -k "not OutsideIP" -v ${PYTEST_ARGS}

# Capture exit code from inside tests
INSIDE_EXIT_CODE=$?

# Run tests from outside allowed network (negative cases)
echo ""
echo "=========================================="
echo "Running CIDR tests from OUTSIDE allowed network (192.168.100.x)..."
echo "These tests verify IPs outside the allow-list CANNOT read without auth"
echo "=========================================="
docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml \
  exec -e MAGPIE_CIDR_ADMIN_TOKEN="$ADMIN_TOKEN" test-runner-outside \
  pytest tests/e2e/test_cidr_allowlist.py::TestCIDRAllowListOutsideIPDenied -v ${PYTEST_ARGS}

# Capture exit code from outside tests
OUTSIDE_EXIT_CODE=$?

# Determine overall exit code
if [ $INSIDE_EXIT_CODE -ne 0 ] || [ $OUTSIDE_EXIT_CODE -ne 0 ]; then
    TEST_EXIT_CODE=1
    echo ""
    echo "=========================================="
    echo "CIDR test failures detected:"
    [ $INSIDE_EXIT_CODE -ne 0 ] && echo "  - Inside network tests: FAILED"
    [ $OUTSIDE_EXIT_CODE -ne 0 ] && echo "  - Outside network tests: FAILED"
    echo "=========================================="
else
    TEST_EXIT_CODE=0
    echo ""
    echo "=========================================="
    echo "All CIDR tests passed!"
    echo "  - Inside network tests: OK"
    echo "  - Outside network tests: OK"
    echo "=========================================="
fi

echo "Cleaning up..."
docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml down

# Exit with test result
exit $TEST_EXIT_CODE
