#!/bin/bash
# Test script for Caddyfile extraction in TLS mode: off
# Verifies fix for issue #273

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CADDYFILE_PROD="${SCRIPT_DIR}/Caddyfile.prod"
TEST_OUTPUT="/tmp/test_caddyfile_extraction_$$.txt"

cleanup() {
    rm -f "$TEST_OUTPUT"
}
trap cleanup EXIT

echo "Testing Caddyfile extraction (issue #273 fix)..."
echo "Source: ${CADDYFILE_PROD}"
echo "Output: ${TEST_OUTPUT}"
echo ""

# Extract site block using the new awk-based method
awk '
    /^{\$MAGPIE_DOMAIN}/ {
        in_site_block = 1
        depth = 0
        next
    }
    in_site_block {
        # Count opening and closing braces
        for (i = 1; i <= length($0); i++) {
            c = substr($0, i, 1)
            if (c == "{") depth++
            if (c == "}") depth--
        }

        # If depth returns to -1, we found the final closing brace
        if (depth == -1) {
            exit
        }

        # Skip HSTS header (not applicable to HTTP-only mode)
        if ($0 !~ /Strict-Transport-Security/) {
            print
        }
    }
' "$CADDYFILE_PROD" > "$TEST_OUTPUT"

# Test 1: Verify we have content
echo -n "Test 1: Checking if extraction produced output... "
if [[ -s "$TEST_OUTPUT" ]]; then
    echo -e "${GREEN}PASS${NC}"
else
    echo -e "${RED}FAIL${NC}"
    echo "ERROR: No output produced"
    exit 1
fi

# Test 2: Verify we have the header block
echo -n "Test 2: Checking for header block... "
if grep -q "header {" "$TEST_OUTPUT"; then
    echo -e "${GREEN}PASS${NC}"
else
    echo -e "${RED}FAIL${NC}"
    echo "ERROR: header block not found"
    exit 1
fi

# Test 3: Verify we have route handlers (not just the header block)
echo -n "Test 3: Checking for route handlers... "
if grep -q "handle /health" "$TEST_OUTPUT" && \
   grep -q "handle /artifacts" "$TEST_OUTPUT" && \
   grep -q "handle /" "$TEST_OUTPUT"; then
    echo -e "${GREEN}PASS${NC}"
else
    echo -e "${RED}FAIL${NC}"
    echo "ERROR: Route handlers not found"
    echo "Expected: handle /health, handle /artifacts, handle /"
    exit 1
fi

# Test 4: Verify root redirect is present
echo -n "Test 4: Checking for root redirect... "
if grep -q "redir /artifacts/ permanent" "$TEST_OUTPUT"; then
    echo -e "${GREEN}PASS${NC}"
else
    echo -e "${RED}FAIL${NC}"
    echo "ERROR: Root redirect not found"
    exit 1
fi

# Test 5: Verify fallback handler is present
echo -n "Test 5: Checking for fallback handler... "
if grep -q 'respond "Not Found" 404' "$TEST_OUTPUT"; then
    echo -e "${GREEN}PASS${NC}"
else
    echo -e "${RED}FAIL${NC}"
    echo "ERROR: Fallback handler not found"
    exit 1
fi

# Test 6: Verify HSTS header is removed
echo -n "Test 6: Checking HSTS header is removed... "
if ! grep -q "Strict-Transport-Security" "$TEST_OUTPUT"; then
    echo -e "${GREEN}PASS${NC}"
else
    echo -e "${RED}FAIL${NC}"
    echo "ERROR: HSTS header should be removed in HTTP-only mode"
    exit 1
fi

# Test 7: Verify we have a reasonable amount of content (should be ~260 lines)
echo -n "Test 7: Checking output length... "
line_count=$(wc -l < "$TEST_OUTPUT")
if [[ $line_count -gt 200 ]]; then
    echo -e "${GREEN}PASS${NC} ($line_count lines)"
else
    echo -e "${RED}FAIL${NC} ($line_count lines)"
    echo "ERROR: Output too short (expected >200 lines, got $line_count)"
    echo "This suggests the extraction stopped too early"
    exit 1
fi

# Test 8: Verify specific API endpoints are present
echo -n "Test 8: Checking for API endpoint handlers... "
if grep -q "@artifacts_read" "$TEST_OUTPUT" && \
   grep -q "@artifacts_tags" "$TEST_OUTPUT" && \
   grep -q "handle /api/v1/tokens" "$TEST_OUTPUT" && \
   grep -q "handle /api/v1/gc" "$TEST_OUTPUT"; then
    echo -e "${GREEN}PASS${NC}"
else
    echo -e "${RED}FAIL${NC}"
    echo "ERROR: Not all API endpoint handlers found"
    exit 1
fi

echo ""
echo -e "${GREEN}All tests passed!${NC}"
echo ""
echo "Sample of extracted content (first 20 lines):"
head -20 "$TEST_OUTPUT"
echo "..."
echo ""
echo "Sample of extracted content (last 20 lines):"
tail -20 "$TEST_OUTPUT"
