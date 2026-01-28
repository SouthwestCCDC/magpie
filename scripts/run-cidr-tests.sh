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

echo "Running CIDR tests inside test-runner container..."
docker compose exec -e MAGPIE_CIDR_ADMIN_TOKEN="$ADMIN_TOKEN" test-runner pytest tests/e2e/test_cidr_allowlist.py "$PYTEST_ARGS"

# Capture exit code
TEST_EXIT_CODE=$?

echo "Cleaning up..."
docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml down

# Exit with test result
exit $TEST_EXIT_CODE
