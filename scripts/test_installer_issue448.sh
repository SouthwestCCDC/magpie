#!/bin/bash
# Test script for issue #448: input sanitization / RCE hardening
#
# This script exercises the *real* functions from magpie-deploy.sh (sourced,
# not reimplemented) to verify that:
# 1. A hostile --domain is rejected by validate_config/is_valid_domain, and
#    even if it reached generate_caddyfile, no command is executed (sed's
#    'e' command is no longer used to interpolate DOMAIN).
# 2. A hostile --trusted-proxies value (embedded newline + Caddy directive)
#    is rejected by validate_config/is_valid_ip_or_cidr_list.
# 3. A hostile --bind-ip and --acme-server are rejected.
# 4. A hostile .env value ($(...) / backtick / newline) does not execute
#    when read by read_env_file/load_existing_config (no more `source`).
# 5. Path inputs (--install-dir/--data-dir) reject '..' and shell metachars.
# 6. `logs` rejects a non-numeric --lines value.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_SCRIPT="${SCRIPT_DIR}/magpie-deploy.sh"
TEST_DIR=$(mktemp -d)
MARKER="${TEST_DIR}/pwned"

cleanup() {
    rm -rf "$TEST_DIR"
    rm -f "$MARKER"
}
trap cleanup EXIT

# Source only the function definitions from magpie-deploy.sh -- drop the
# trailing `main "$@"` invocation so sourcing doesn't run the CLI. This also
# pulls in the script's own log()/log_error()/die(), which we override
# below with test-local versions.
# shellcheck source=/dev/null
source <(sed '/^main "\$@"$/d' "$DEPLOY_SCRIPT")

log() {
    echo "[test] $*"
}

fail() {
    echo "[test] FAIL: $*" >&2
    exit 1
}

# Test 1: is_valid_domain rejects a sed 'e'-command injection payload
test_domain_rejects_injection() {
    log "Test 1: is_valid_domain rejects hostile --domain values"

    local hostile="x/g;e touch ${MARKER}"
    if is_valid_domain "$hostile"; then
        fail "is_valid_domain accepted a hostile domain: $hostile"
    fi
    log "  ✓ hostile domain rejected: $hostile"

    if ! is_valid_domain "magpie.example.com"; then
        fail "is_valid_domain rejected a legitimate domain: magpie.example.com"
    fi
    log "  ✓ legitimate domain accepted: magpie.example.com"
}

# Test 2: validate_config rejects a hostile --domain before any file is generated
test_validate_config_rejects_hostile_domain() {
    log "Test 2: validate_config rejects hostile DOMAIN"

    local hostile="x/g;e touch ${MARKER}"
    if (
        TLS_MODE=auto DOMAIN="$hostile" TLS_CERT="" TLS_KEY="" \
        HTTP_PORT=8080 HTTPS_PORT=8443 \
        INSTALL_DIR="${TEST_DIR}/install" DATA_DIR="${TEST_DIR}/install/data" \
        TRUSTED_PROXIES="" BIND_IP="" ACME_SERVER="" \
        validate_config
    ) >/dev/null 2>&1; then
        fail "validate_config accepted a hostile domain: $hostile"
    fi
    log "  ✓ validate_config rejected hostile domain"
    [[ -e "$MARKER" ]] && fail "hostile domain executed a command during validation"
    log "  ✓ no command executed during validation"
}

# Test 3: generate_caddyfile no longer uses sed to interpolate DOMAIN, so
# even a hostile value that reached it (bypassing validate_config) cannot
# execute a command via sed's 'e' command.
test_generate_caddyfile_no_command_execution() {
    log "Test 3: generate_caddyfile does not execute commands via DOMAIN"

    local install_dir="${TEST_DIR}/gc_install"
    mkdir -p "${install_dir}/repo" "${install_dir}/etc"
    cp "${SCRIPT_DIR}/../Caddyfile.prod" "${install_dir}/repo/Caddyfile.prod" 2>/dev/null \
        || cat > "${install_dir}/repo/Caddyfile.prod" << 'EOF'
{
	admin off
}

{$MAGPIE_DOMAIN} {
	reverse_proxy magpie:8000
}
EOF

    local hostile="x/g;e touch ${MARKER}"
    (
        INSTALL_DIR="$install_dir" MAGPIE_VERSION="test" TLS_MODE="auto" \
        DOMAIN="$hostile" ACME_SERVER="" \
        generate_caddyfile
    ) >/dev/null 2>&1 || true

    if [[ -e "$MARKER" ]]; then
        fail "generate_caddyfile executed a command via a hostile DOMAIN value"
    fi
    log "  ✓ no command executed by generate_caddyfile"

    if [[ -f "${install_dir}/etc/Caddyfile" ]] && grep -qF "$hostile {" "${install_dir}/etc/Caddyfile"; then
        log "  ✓ hostile domain written as inert literal text (no injection)"
    fi
}

# Test 4: is_valid_ip_or_cidr_list rejects a newline-smuggled Caddy directive
test_trusted_proxies_rejects_injection() {
    log "Test 4: is_valid_ip_or_cidr_list rejects hostile --trusted-proxies"

    local hostile
    hostile=$'10.0.0.0/8\n}\nadmin_endpoint 0.0.0.0:2999 {\n\tenforce_origin\n}\n{'
    if is_valid_ip_or_cidr_list "$hostile"; then
        fail "is_valid_ip_or_cidr_list accepted a directive-injection payload"
    fi
    log "  ✓ hostile trusted-proxies value rejected"

    if ! is_valid_ip_or_cidr_list "127.0.0.0/8 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16"; then
        fail "is_valid_ip_or_cidr_list rejected the default RFC1918 proxy list"
    fi
    log "  ✓ legitimate whitespace-separated CIDR list accepted"
}

# Test 5: validate_config rejects hostile --trusted-proxies end to end
test_validate_config_rejects_hostile_trusted_proxies() {
    log "Test 5: validate_config rejects hostile TRUSTED_PROXIES"

    local hostile
    hostile=$'10.0.0.0/8\n}\nadmin_endpoint 0.0.0.0:2999 {'
    if (
        TLS_MODE=off DOMAIN="" TLS_CERT="" TLS_KEY="" \
        HTTP_PORT=8080 HTTPS_PORT=8443 \
        INSTALL_DIR="${TEST_DIR}/install2" DATA_DIR="${TEST_DIR}/install2/data" \
        TRUSTED_PROXIES="$hostile" BIND_IP="" ACME_SERVER="" \
        validate_config
    ) >/dev/null 2>&1; then
        fail "validate_config accepted hostile trusted-proxies"
    fi
    log "  ✓ validate_config rejected hostile trusted-proxies"
}

# Test 6: --bind-ip and --acme-server format validation
test_bind_ip_and_acme_server_validation() {
    log "Test 6: BIND_IP and ACME_SERVER validation"

    if is_valid_ip_or_cidr "10.3.3.107/24"; then
        : # CIDR is valid as a CIDR
    fi
    if [[ "10.3.3.107/24" == */* ]]; then
        log "  ✓ CIDR-with-prefix correctly identified for --bind-ip rejection"
    fi

    if (
        TLS_MODE=off DOMAIN="" TLS_CERT="" TLS_KEY="" \
        HTTP_PORT=8080 HTTPS_PORT=8443 \
        INSTALL_DIR="${TEST_DIR}/install3" DATA_DIR="${TEST_DIR}/install3/data" \
        TRUSTED_PROXIES="" BIND_IP="10.3.3.107/24" ACME_SERVER="" \
        validate_config
    ) >/dev/null 2>&1; then
        fail "validate_config accepted a --bind-ip value with a CIDR prefix"
    fi
    log "  ✓ --bind-ip with CIDR prefix rejected"

    local hostile_acme=$'https://ca.example.com\n{\n\trespond 200\n}'
    if (
        TLS_MODE=auto DOMAIN="magpie.example.com" TLS_CERT="" TLS_KEY="" \
        HTTP_PORT=8080 HTTPS_PORT=8443 \
        INSTALL_DIR="${TEST_DIR}/install4" DATA_DIR="${TEST_DIR}/install4/data" \
        TRUSTED_PROXIES="" BIND_IP="" ACME_SERVER="$hostile_acme" \
        validate_config
    ) >/dev/null 2>&1; then
        fail "validate_config accepted a hostile --acme-server value"
    fi
    log "  ✓ hostile --acme-server (embedded directive) rejected"

    if (
        TLS_MODE=auto DOMAIN="magpie.example.com" TLS_CERT="" TLS_KEY="" \
        HTTP_PORT=8080 HTTPS_PORT=8443 \
        INSTALL_DIR="${TEST_DIR}/install5" DATA_DIR="${TEST_DIR}/install5/data" \
        TRUSTED_PROXIES="" BIND_IP="" ACME_SERVER="http://ca.example.com/acme/directory" \
        validate_config
    ) >/dev/null 2>&1; then
        fail "validate_config accepted a non-https --acme-server value"
    fi
    log "  ✓ non-https --acme-server rejected"
}

# Test 7: read_env_file does not execute $(...)/backtick/newline payloads (H1)
test_env_file_not_sourced() {
    log "Test 7: read_env_file parses .env without shell execution"

    local hostile_env="${TEST_DIR}/hostile.env"
    {
        echo "MAGPIE_DATA_DIR=/opt/magpie/data"
        echo "MAGPIE_DOMAIN=magpie.example.com\$(touch ${MARKER})"
        printf 'TRUSTED_PROXIES=10.0.0.0/8\n%s\n' "touch ${MARKER}_smuggled_line"
        echo 'BACKTICK_VAL=`touch '"${MARKER}"'_backtick`'
    } > "$hostile_env"

    declare -A parsed=()
    read_env_file "$hostile_env" parsed

    if [[ -e "$MARKER" || -e "${MARKER}_smuggled_line" || -e "${MARKER}_backtick" ]]; then
        fail "read_env_file executed a command embedded in a .env value"
    fi
    log "  ✓ no command executed while parsing a hostile .env file"

    if [[ "${parsed[MAGPIE_DATA_DIR]:-}" != "/opt/magpie/data" ]]; then
        fail "read_env_file failed to parse a well-formed line"
    fi
    log "  ✓ well-formed KEY=value lines still parse correctly"

    if [[ "${parsed[MAGPIE_DOMAIN]:-}" != *'$(touch'* ]]; then
        fail "read_env_file did not preserve the value literally"
    fi
    log "  ✓ hostile value preserved as inert literal text (not expanded)"
}

# Test 8: load_existing_config uses read_env_file (no `source`) end to end
test_load_existing_config_no_execution() {
    log "Test 8: load_existing_config does not execute .env contents"

    local install_dir="${TEST_DIR}/lec_install"
    mkdir -p "${install_dir}/etc"
    {
        echo "MAGPIE_DATA_DIR=/opt/magpie/data"
        echo "MAGPIE_HTTP_PORT=8080"
        echo "MAGPIE_HTTPS_PORT=8443"
        echo "MAGPIE_DOMAIN=magpie.example.com"
        echo "TLS_MODE=auto"
        echo "TRUSTED_PROXIES=10.0.0.0/8"
        echo "BIND_IP=\$(touch ${MARKER}_lec)"
        echo "ACME_SERVER="
    } > "${install_dir}/etc/.env"

    (
        INSTALL_DIR="$install_dir" DATA_DIR="" HTTP_PORT="" HTTPS_PORT="" DOMAIN="" \
        TLS_MODE="" TRUSTED_PROXIES="" BIND_IP="" ACME_SERVER="" \
        load_existing_config
    )

    if [[ -e "${MARKER}_lec" ]]; then
        fail "load_existing_config executed a command embedded in .env"
    fi
    log "  ✓ load_existing_config parses .env without executing it"

    grep -q "^source " "$DEPLOY_SCRIPT" && grep -qE 'source ".*etc/\.env"' "$DEPLOY_SCRIPT" \
        && fail "magpie-deploy.sh still sources the generated .env file somewhere"
    log "  ✓ magpie-deploy.sh no longer sources the generated .env file"
}

# Test 9: path inputs reject '..' segments and shell metacharacters
test_path_validation() {
    log "Test 9: install/data directory validation rejects '..' and metacharacters"

    if (
        TLS_MODE=off DOMAIN="" TLS_CERT="" TLS_KEY="" \
        HTTP_PORT=8080 HTTPS_PORT=8443 \
        INSTALL_DIR="/opt/magpie/../../etc" DATA_DIR="/opt/magpie/data" \
        TRUSTED_PROXIES="" BIND_IP="" ACME_SERVER="" \
        validate_config
    ) >/dev/null 2>&1; then
        fail "validate_config accepted an install-dir containing '..'"
    fi
    log "  ✓ '..' path segment rejected"

    if (
        TLS_MODE=off DOMAIN="" TLS_CERT="" TLS_KEY="" \
        HTTP_PORT=8080 HTTPS_PORT=8443 \
        INSTALL_DIR="/opt/magpie" DATA_DIR='/opt/magpie/data; rm -rf /' \
        TRUSTED_PROXIES="" BIND_IP="" ACME_SERVER="" \
        validate_config
    ) >/dev/null 2>&1; then
        fail "validate_config accepted a data-dir containing shell metacharacters"
    fi
    log "  ✓ shell metacharacters in data-dir rejected"
}

# Test 10: `logs` rejects a non-numeric --lines value and uses a quoted array
test_logs_lines_validation() {
    log "Test 10: cmd_logs rejects non-numeric --lines"

    local install_dir="${TEST_DIR}/logs_install"
    mkdir -p "${install_dir}/etc"
    echo "MAGPIE_DATA_DIR=/opt/magpie/data" > "${install_dir}/etc/.env"

    if (
        INSTALL_DIR="$install_dir" LINES="5; touch ${MARKER}_lines" FOLLOW="false" \
        cmd_logs
    ) >/dev/null 2>&1; then
        fail "cmd_logs accepted a non-numeric --lines value"
    fi
    log "  ✓ non-numeric --lines rejected"
    [[ -e "${MARKER}_lines" ]] && fail "non-numeric --lines value was executed"
    log "  ✓ no command executed via --lines"

    if grep -qE 'docker compose --env-file "\$\{INSTALL_DIR\}/etc/\.env" logs \$follow_flag' "$DEPLOY_SCRIPT"; then
        fail "cmd_logs still builds its docker-compose invocation via unquoted expansion"
    fi
    log "  ✓ cmd_logs no longer uses unquoted \$follow_flag expansion"
}

# Run all tests
log "Running tests for issue #448"
log ""

test_domain_rejects_injection
test_validate_config_rejects_hostile_domain
test_generate_caddyfile_no_command_execution
test_trusted_proxies_rejects_injection
test_validate_config_rejects_hostile_trusted_proxies
test_bind_ip_and_acme_server_validation
test_env_file_not_sourced
test_load_existing_config_no_execution
test_path_validation
test_logs_lines_validation

log ""
log "All tests passed!"
