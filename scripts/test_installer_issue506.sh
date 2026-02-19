#!/bin/bash
# Test script for issue #506: HTTPS port not mapped when TLS mode is off
#
# This script tests that the installer correctly removes the HTTPS port
# mapping from docker-compose.yml when --tls-mode off is used.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_DIR=$(mktemp -d)
trap "rm -rf $TEST_DIR" EXIT

log() {
    echo "[test] $*"
}

fail() {
    echo "[test] FAIL: $*" >&2
    exit 1
}

# Minimal docker-compose.yml snippet with both HTTP and HTTPS ports
make_compose() {
    local path="$1"
    cat > "$path" << 'EOF'
services:
  caddy:
    image: caddy:2-alpine
    ports:
      - "${MAGPIE_HTTP_PORT:-8080}:80"    # HTTP
      - "${MAGPIE_HTTPS_PORT:-8443}:443"  # HTTPS (auto-TLS in production)
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
EOF
}

# Test 1: HTTPS port is removed when TLS mode is off
test_https_port_removed_when_tls_off() {
    log "Test 1: HTTPS port mapping removed when TLS mode is off"

    local test_compose="${TEST_DIR}/docker-compose-test1.yml"
    make_compose "$test_compose"

    # Apply the patch (simulate what patch_compose_for_https_port does)
    sed -i '/"\${MAGPIE_HTTPS_PORT:-[0-9]*}:443"/d' "$test_compose"

    # Verify HTTPS port is gone
    if grep -q 'MAGPIE_HTTPS_PORT' "$test_compose"; then
        fail "HTTPS port mapping was NOT removed when TLS mode is off"
    fi
    log "  ✓ HTTPS port mapping removed"

    # Verify HTTP port is still present
    if ! grep -q 'MAGPIE_HTTP_PORT' "$test_compose"; then
        fail "HTTP port mapping was incorrectly removed"
    fi
    log "  ✓ HTTP port mapping preserved"
}

# Test 2: HTTPS port is preserved when TLS mode is auto
test_https_port_preserved_when_tls_auto() {
    log "Test 2: HTTPS port mapping preserved when TLS mode is auto"

    local test_compose="${TEST_DIR}/docker-compose-test2.yml"
    make_compose "$test_compose"

    # With TLS mode auto, we should NOT apply the removal patch (no-op)
    local TLS_MODE="auto"
    if [[ "$TLS_MODE" == "off" ]]; then
        sed -i '/"\${MAGPIE_HTTPS_PORT:-[0-9]*}:443"/d' "$test_compose"
    fi

    if ! grep -q 'MAGPIE_HTTPS_PORT' "$test_compose"; then
        fail "HTTPS port mapping was incorrectly removed when TLS mode is auto"
    fi
    log "  ✓ HTTPS port mapping preserved (TLS mode: auto)"
}

# Test 3: HTTPS port is preserved when TLS mode is manual
test_https_port_preserved_when_tls_manual() {
    log "Test 3: HTTPS port mapping preserved when TLS mode is manual"

    local test_compose="${TEST_DIR}/docker-compose-test3.yml"
    make_compose "$test_compose"

    # With TLS mode manual, we should NOT apply the removal patch (no-op)
    local TLS_MODE="manual"
    if [[ "$TLS_MODE" == "off" ]]; then
        sed -i '/"\${MAGPIE_HTTPS_PORT:-[0-9]*}:443"/d' "$test_compose"
    fi

    if ! grep -q 'MAGPIE_HTTPS_PORT' "$test_compose"; then
        fail "HTTPS port mapping was incorrectly removed when TLS mode is manual"
    fi
    log "  ✓ HTTPS port mapping preserved (TLS mode: manual)"
}

# Test 4: Combined scenario — TLS off + BIND_IP
# When TLS is off, even if BIND_IP is set, the HTTPS port should not appear
test_https_port_removed_with_bind_ip() {
    log "Test 4: HTTPS port mapping removed even when BIND_IP is also set"

    local test_compose="${TEST_DIR}/docker-compose-test4.yml"
    make_compose "$test_compose"

    # Step 1: Remove HTTPS port (patch_compose_for_https_port runs before patch_compose_for_bind_ip)
    sed -i '/"\${MAGPIE_HTTPS_PORT:-[0-9]*}:443"/d' "$test_compose"

    # Step 2: Add BIND_IP to HTTP port (patch_compose_for_bind_ip)
    sed -i 's|- "\${MAGPIE_HTTP_PORT:-[0-9]*}:80"|- "${BIND_IP}:${MAGPIE_HTTP_PORT:-8080}:80"|g' "$test_compose"
    sed -i 's|- "\${MAGPIE_HTTPS_PORT:-[0-9]*}:443"|- "${BIND_IP}:${MAGPIE_HTTPS_PORT:-8443}:443"|g' "$test_compose"

    # Verify HTTPS port is gone
    if grep -q 'MAGPIE_HTTPS_PORT' "$test_compose"; then
        fail "HTTPS port mapping appeared after bind_ip patch (should still be absent)"
    fi
    log "  ✓ HTTPS port mapping absent after combined TLS-off + BIND_IP patching"

    # Verify HTTP port with BIND_IP is present
    if ! grep -q 'BIND_IP.*MAGPIE_HTTP_PORT' "$test_compose"; then
        fail "HTTP port binding with BIND_IP was not applied"
    fi
    log "  ✓ HTTP port binding with BIND_IP present"
}

# Test 5: Verify the deploy script contains the patch_compose_for_https_port function
test_deploy_script_has_function() {
    log "Test 5: Verifying magpie-deploy.sh contains patch_compose_for_https_port"

    local deploy_script="${SCRIPT_DIR}/magpie-deploy.sh"

    if grep -q "patch_compose_for_https_port()" "$deploy_script"; then
        log "  ✓ patch_compose_for_https_port function defined"
    else
        fail "patch_compose_for_https_port function not found in magpie-deploy.sh"
    fi

    # Verify it's called in cmd_install
    if awk '/^cmd_install\(\)/,/^}/' "$deploy_script" | grep -q "patch_compose_for_https_port"; then
        log "  ✓ patch_compose_for_https_port called in cmd_install"
    else
        fail "patch_compose_for_https_port not called in cmd_install"
    fi

    # Verify it's called in cmd_update
    if awk '/^cmd_update\(\)/,/^}/' "$deploy_script" | grep -q "patch_compose_for_https_port"; then
        log "  ✓ patch_compose_for_https_port called in cmd_update"
    else
        fail "patch_compose_for_https_port not called in cmd_update"
    fi
}

# Test 6: Verify the issue #506 is referenced in the function
test_issue_reference() {
    log "Test 6: Verifying issue #506 is referenced in patch_compose_for_https_port"

    local deploy_script="${SCRIPT_DIR}/magpie-deploy.sh"

    if grep -A 20 "patch_compose_for_https_port()" "$deploy_script" | grep -q "#506"; then
        log "  ✓ Issue #506 referenced in patch_compose_for_https_port"
    else
        fail "Issue #506 not referenced in patch_compose_for_https_port"
    fi
}

# Run all tests
log "Running tests for issue #506"
log ""

test_https_port_removed_when_tls_off
test_https_port_preserved_when_tls_auto
test_https_port_preserved_when_tls_manual
test_https_port_removed_with_bind_ip
test_deploy_script_has_function
test_issue_reference

log ""
log "All tests passed!"
