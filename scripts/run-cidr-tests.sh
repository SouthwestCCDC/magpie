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
#   ./scripts/run-cidr-tests.sh                    # Run all CIDR tests (verbose, short traceback)
#   ./scripts/run-cidr-tests.sh -vv                # Very verbose output
#
# Note: This script filters tests by class name to run inside/outside tests
# separately. Custom -k filters are not supported. To run specific tests,
# use docker compose exec directly (see tests/e2e/README_CIDR_TESTS.md).

set -euo pipefail

# Cleanup function to ensure containers are removed on exit
cleanup() {
    echo "Cleaning up..."
    docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml down -v
}
trap cleanup EXIT

# Change to project root
cd "$(dirname "$0")/.."

# Default pytest args if none provided
if [ $# -eq 0 ]; then
    PYTEST_ARGS=("-v" "--tb=short")
else
    PYTEST_ARGS=("$@")
fi

echo "Starting CIDR test environment..."
docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml up -d --build

# Wait for services to be healthy with retry loop
echo "Waiting for services to be healthy..."

# Check curl availability first
if ! command -v curl &> /dev/null; then
    echo "ERROR: curl is not installed. Please install curl or use docker compose ps to check health status."
    exit 1
fi

# Health check with retry loop (similar to magpie-deploy.sh wait_for_healthy)
max_attempts=30
attempt=1
health_url="http://localhost:8080/health"

while [ $attempt -le $max_attempts ]; do
    if curl -sf "$health_url" > /dev/null 2>&1; then
        echo "Services are healthy (attempt $attempt/$max_attempts)"
        break
    fi
    echo -n "."
    sleep 2
    attempt=$((attempt + 1))
done

# Check if we exhausted all attempts
if [ $attempt -gt $max_attempts ]; then
    echo ""
    echo "ERROR: Services failed to become healthy after $max_attempts attempts. Check logs:"
    docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml logs
    exit 1
fi

# Initialize magpie and get admin token
echo "Initializing magpie and getting admin token..."
# Capture output and return code separately to avoid mixing stdout/stderr
INIT_OUTPUT=$(docker compose exec -T magpie magpie-ctl init --reset-admin-token 2>&1)
INIT_EXIT_CODE=$?

if [ $INIT_EXIT_CODE -ne 0 ]; then
    echo "ERROR: magpie-ctl init failed with exit code $INIT_EXIT_CODE"
    echo "Output: $INIT_OUTPUT"
    docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml logs magpie
    exit 1
fi

# Extract token from stdout only after confirming success
ADMIN_TOKEN=$(echo "$INIT_OUTPUT" | grep "^mgp_" | head -1)

if [ -z "$ADMIN_TOKEN" ]; then
    echo "ERROR: Failed to extract admin token from output"
    echo "Output: $INIT_OUTPUT"
    exit 1
fi

echo "Admin token obtained: ${ADMIN_TOKEN:0:10}..."

# Run tests from inside allowed network (positive cases)
echo ""
echo "=========================================="
echo "Running CIDR tests from INSIDE allowed network (172.18.0.x)..."
echo "These tests verify IPs in the allow-list CAN read without auth"
echo "=========================================="

# Temporarily disable errexit to allow tests to fail without stopping the script
set +e
docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml \
  exec -e MAGPIE_CIDR_ADMIN_TOKEN="$ADMIN_TOKEN" test-runner-inside \
  pytest tests/e2e/test_cidr_allowlist.py -k "not OutsideIP" "${PYTEST_ARGS[@]}"

# Capture exit code from inside tests
INSIDE_EXIT_CODE=$?
# Re-enable errexit
set -e

# Run tests from outside allowed network (negative cases)
echo ""
echo "=========================================="
echo "Running CIDR tests from OUTSIDE allowed network (192.168.100.x)..."
echo "These tests verify IPs outside the allow-list CANNOT read without auth"
echo "=========================================="

# Temporarily disable errexit to allow tests to fail without stopping the script
set +e
docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml \
  exec -e MAGPIE_CIDR_ADMIN_TOKEN="$ADMIN_TOKEN" test-runner-outside \
  pytest tests/e2e/test_cidr_allowlist.py::TestCIDRAllowListOutsideIPDenied \
  tests/e2e/test_cidr_allowlist.py::TestCIDRAllowListTokenInteraction::test_outside_cidr_with_valid_read_token \
  tests/e2e/test_cidr_allowlist.py::TestCIDRAllowListTokenInteraction::test_outside_cidr_with_valid_write_token \
  "${PYTEST_ARGS[@]}"

# Capture exit code from outside tests
OUTSIDE_EXIT_CODE=$?
# Re-enable errexit
set -e

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

# Exit with test result (cleanup handled by trap)
exit $TEST_EXIT_CODE
