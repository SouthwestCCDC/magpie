#!/bin/bash
# Test script for issue #448: input sanitization / RCE hardening
#
# This script exercises the *real* functions from magpie-deploy.sh (sourced,
# not reimplemented) to verify that:
# 1. A hostile --trusted-proxies value (embedded newline + Caddy directive)
#    is rejected by validate_config/is_valid_ip_or_cidr_list.
# 2. A hostile --bind-ip is rejected.
# 3. A hostile .env value ($(...) / backtick / newline) does not execute
#    when read by read_env_file/load_existing_config (no more `source`).
# 4. Path inputs (--install-dir/--data-dir) reject '..' and shell metachars.
# 5. `logs` rejects a non-numeric --lines value.
# 6. `uninstall --purge` refuses to `rm -rf` a DATA_DIR loaded from a
#    hostile .env.
# 7. `update` rejects an --install-dir containing sed metacharacters
#    (e.g. '|') before it reaches any -f docker-compose.yml invocation.
# 8. IPv4/IPv6 CIDR validation does not misjudge or error on leading-zero
#    octets/prefixes (bash arithmetic octal gotcha).
# 9. A newline sitting between two otherwise-valid --trusted-proxies
#    tokens is rejected up front, not left to per-token validation (which
#    would pass it, since each half is individually a valid IP/CIDR).
# 10. A tab-separated --trusted-proxies list is still accepted (tab is a
#     legitimate whitespace separator, not rejected as a control char).
# 11. The "no source .env" test assertion actually detects a reintroduced
#     source line (verified against the anchoring bug that made it
#     vacuously pass regardless of whether .env was sourced).
# 12. read_env_file() strips a trailing CR so a CRLF-terminated (e.g.
#     Windows-edited) .env doesn't leave a stray \r embedded in values.
#
# NOTE: the domain/TLS-cert/TLS-key/ACME-server-specific tests that used to
# live here (is_valid_domain, is_valid_acme_server_url, generate_caddyfile
# injection/escape-decoding cases) were removed along with the functions
# they exercised -- the bundled image (v0.2.0) has no in-container TLS or
# Caddyfile to configure. See scripts/magpie-deploy.sh's Tier 1 deprecation
# handling in parse_args() for what replaced them.

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

# Sourcing magpie-deploy.sh above also executes its own top-level
# `set -euo pipefail`, which would leave `errexit` enabled here. That's
# unwanted in this test script: several tests intentionally exercise a
# function's EXPECTED-FAILURE path, and while those calls are wrapped in
# explicit subshells or `if` conditions (both errexit-safe), a stray
# unprotected failing command anywhere else could otherwise abort the
# whole run silently instead of reporting a clear FAIL. Re-assert only the
# options this test script itself wants.
set +e
set -uo pipefail

log() {
    echo "[test] $*"
}

fail() {
    echo "[test] FAIL: $*" >&2
    exit 1
}

# Test 1: is_valid_ip_or_cidr_list rejects a newline-smuggled Caddy directive
test_trusted_proxies_rejects_injection() {
    log "Test 1: is_valid_ip_or_cidr_list rejects hostile --trusted-proxies"

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

# Test 2: validate_config rejects hostile --trusted-proxies end to end
test_validate_config_rejects_hostile_trusted_proxies() {
    log "Test 2: validate_config rejects hostile TRUSTED_PROXIES"

    local hostile
    hostile=$'10.0.0.0/8\n}\nadmin_endpoint 0.0.0.0:2999 {'
    if (
        HTTP_PORT=8080 \
        INSTALL_DIR="${TEST_DIR}/install2" DATA_DIR="${TEST_DIR}/install2/data" \
        TRUSTED_PROXIES="$hostile" BIND_IP="" \
        validate_config
    ) >/dev/null 2>&1; then
        fail "validate_config accepted hostile trusted-proxies"
    fi
    log "  ✓ validate_config rejected hostile trusted-proxies"
}

# Test 3: --bind-ip format validation
test_bind_ip_validation() {
    log "Test 3: BIND_IP validation"

    if [[ "10.3.3.107/24" == */* ]]; then
        log "  ✓ CIDR-with-prefix correctly identified for --bind-ip rejection"
    fi

    if (
        HTTP_PORT=8080 \
        INSTALL_DIR="${TEST_DIR}/install3" DATA_DIR="${TEST_DIR}/install3/data" \
        TRUSTED_PROXIES="" BIND_IP="10.3.3.107/24" \
        validate_config
    ) >/dev/null 2>&1; then
        fail "validate_config accepted a --bind-ip value with a CIDR prefix"
    fi
    log "  ✓ --bind-ip with CIDR prefix rejected"

    if (
        HTTP_PORT=8080 \
        INSTALL_DIR="${TEST_DIR}/install3b" DATA_DIR="${TEST_DIR}/install3b/data" \
        TRUSTED_PROXIES="" BIND_IP="10.3.3.107" \
        validate_config
    ) >/dev/null 2>&1; then
        :
    else
        fail "validate_config rejected a legitimate single --bind-ip address"
    fi
    log "  ✓ legitimate single --bind-ip address accepted"
}

# Test 4: read_env_file does not execute $(...)/backtick/newline payloads (H1)
test_env_file_not_sourced() {
    log "Test 4: read_env_file parses .env without shell execution"

    local hostile_env="${TEST_DIR}/hostile.env"
    {
        echo "MAGPIE_DATA_DIR=/opt/magpie/data"
        echo "MAGPIE_IMAGE=ghcr.io/southwestccdc/magpie:test\$(touch ${MARKER})"
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

    if [[ "${parsed[MAGPIE_IMAGE]:-}" != *'$(touch'* ]]; then
        fail "read_env_file did not preserve the value literally"
    fi
    log "  ✓ hostile value preserved as inert literal text (not expanded)"
}

# Test 5: load_existing_config uses read_env_file (no `source`) end to end
test_load_existing_config_no_execution() {
    log "Test 5: load_existing_config does not execute .env contents"

    local install_dir="${TEST_DIR}/lec_install"
    mkdir -p "${install_dir}/etc"
    {
        echo "MAGPIE_DATA_DIR=/opt/magpie/data"
        echo "MAGPIE_HTTP_PORT=8080"
        echo "TLS_MODE=auto"
        echo "TRUSTED_PROXIES=10.0.0.0/8"
        echo "MAGPIE_BIND_IP=\$(touch ${MARKER}_lec)"
    } > "${install_dir}/etc/.env"

    local out
    out=$(
        (
            INSTALL_DIR="$install_dir" DATA_DIR="" HTTP_PORT="" \
            TRUSTED_PROXIES="" BIND_IP="" \
            load_existing_config
            echo "PERSISTED_TLS_MODE=$PERSISTED_TLS_MODE"
        )
    )

    if [[ -e "${MARKER}_lec" ]]; then
        fail "load_existing_config executed a command embedded in .env"
    fi
    log "  ✓ load_existing_config parses .env without executing it"

    if [[ "$out" != "PERSISTED_TLS_MODE=auto" ]]; then
        fail "load_existing_config did not detect the persisted TLS_MODE for the tier-2 gate: $out"
    fi
    log "  ✓ PERSISTED_TLS_MODE detected (read-only; never used to drive generated config)"

    # Anchored on start-of-line with no leading whitespace allowed, this
    # check would never match (magpie-deploy.sh's only literal `source`
    # invocation, `source /etc/os-release` in check_os(), is indented) --
    # making the assertion below vacuously true regardless of whether the
    # .env is sourced. Allow leading whitespace and the `.` dot-command
    # synonym, and require "etc/.env" on the same line so it stays
    # specific to the file this test cares about.
    if grep -qE '(^|[[:space:]])(source|\.)[[:space:]]+.*etc/\.env' "$DEPLOY_SCRIPT"; then
        fail "magpie-deploy.sh still sources the generated .env file somewhere"
    fi
    log "  ✓ magpie-deploy.sh no longer sources the generated .env file"
}

# Test 6: path inputs reject '..' segments and shell metacharacters
test_path_validation() {
    log "Test 6: install/data directory validation rejects '..' and metacharacters"

    if (
        HTTP_PORT=8080 \
        INSTALL_DIR="/opt/magpie/../../etc" DATA_DIR="/opt/magpie/data" \
        TRUSTED_PROXIES="" BIND_IP="" \
        validate_config
    ) >/dev/null 2>&1; then
        fail "validate_config accepted an install-dir containing '..'"
    fi
    log "  ✓ '..' path segment rejected"

    if (
        HTTP_PORT=8080 \
        INSTALL_DIR="/opt/magpie" DATA_DIR='/opt/magpie/data; rm -rf /' \
        TRUSTED_PROXIES="" BIND_IP="" \
        validate_config
    ) >/dev/null 2>&1; then
        fail "validate_config accepted a data-dir containing shell metacharacters"
    fi
    log "  ✓ shell metacharacters in data-dir rejected"
}

# Test 7: `logs` rejects a non-numeric --lines value and uses a quoted array
test_logs_lines_validation() {
    log "Test 7: cmd_logs rejects non-numeric --lines"

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

    if ! grep -qE 'docker compose "\$\{compose_args\[@\]\}"' "$DEPLOY_SCRIPT"; then
        fail "cmd_logs no longer builds its docker-compose invocation via a quoted array"
    fi
    log "  ✓ cmd_logs still uses a quoted compose_args array (no unquoted expansion)"
}

# Test 8: `uninstall --purge` must refuse to `rm -rf` a DATA_DIR that was
# loaded from a hostile/stale .env, not just trust whatever load_existing_config
# populated it with.
test_uninstall_purge_revalidates_data_dir() {
    log "Test 8: cmd_uninstall --purge refuses a hostile DATA_DIR loaded from .env"

    local install_dir="${TEST_DIR}/uninstall_install"
    mkdir -p "${install_dir}/etc"
    echo "MAGPIE_DATA_DIR=/opt/magpie/data; touch ${MARKER}_purge" > "${install_dir}/etc/.env"

    local out
    if out=$(
        (
            INSTALL_DIR="$install_dir" PURGE="true" YES="true" NONINTERACTIVE="true" \
            cmd_uninstall
        ) 2>&1
    ); then
        fail "cmd_uninstall --purge accepted a hostile DATA_DIR from .env"
    fi

    if ! echo "$out" | grep -q "failed validation"; then
        fail "cmd_uninstall --purge did not report a validation failure for the hostile DATA_DIR: $out"
    fi
    log "  ✓ cmd_uninstall --purge refused a hostile DATA_DIR before rm -rf"

    [[ -e "${MARKER}_purge" ]] && fail "hostile DATA_DIR content was executed"
    log "  ✓ no command executed via the hostile DATA_DIR value"
}

# Test 9: `update` must reject an --install-dir containing sed
# metacharacters (e.g. '|') before it reaches any of the `-f
# ${INSTALL_DIR}/docker-compose.yml` / .env-surgery sed invocations.
test_update_rejects_hostile_install_dir() {
    log "Test 9: cmd_update rejects an --install-dir containing sed metacharacters"

    local out
    if out=$(
        (
            INSTALL_DIR="/opt/magpie|s/foo/bar/;x" cmd_update
        ) 2>&1
    ); then
        fail "cmd_update accepted an --install-dir containing '|'"
    fi

    if ! echo "$out" | grep -qE "Invalid --install-dir|invalid characters"; then
        fail "cmd_update did not report an install-dir validation failure: $out"
    fi
    log "  ✓ cmd_update rejected a pipe-containing --install-dir before any patching"
}

# Test 10: leading-zero IPv4 octets / CIDR prefixes must not be misjudged
# (bash arithmetic treats a leading zero as octal) or throw a stderr error.
test_cidr_leading_zero_octets() {
    log "Test 10: IPv4/IPv6 CIDR validation handles leading-zero octets/prefixes correctly"

    local err_output
    err_output=$(is_valid_ipv4_cidr "10.0.008.1" 2>&1 1>/dev/null)
    if [[ -n "$err_output" ]]; then
        fail "is_valid_ipv4_cidr printed a stderr error for a leading-zero octet: $err_output"
    fi
    if ! is_valid_ipv4_cidr "10.0.008.1"; then
        fail "is_valid_ipv4_cidr rejected a valid leading-zero octet (008 == 8, <= 255)"
    fi
    log "  ✓ leading-zero octet '008' handled without error, correctly accepted"

    if ! is_valid_ipv4_cidr "10.0.017.1"; then
        fail "is_valid_ipv4_cidr rejected 10.0.017.1 (017 == 17 in base 10, <= 255)"
    fi
    log "  ✓ leading-zero octet '017' evaluated as base-10 17, not octal 15"

    if is_valid_ipv4_cidr "10.0.0.1/256"; then
        fail "is_valid_ipv4_cidr accepted an out-of-range CIDR prefix"
    fi
    log "  ✓ out-of-range CIDR prefix still rejected"
}

# Test 11: a newline sitting *between* two otherwise-valid IP/CIDR tokens
# must be rejected. Splitting on IFS whitespace (which includes newline)
# means each half of "10.0.0.0/8\n192.168.1.1" is individually a valid
# token, so a purely per-token validator lets the newline through even
# though every token "looks like" a CIDR (Copilot review finding). Also
# confirms tokenization no longer globs (`read -ra`, not `for t in $list`).
test_trusted_proxies_rejects_newline_between_valid_tokens() {
    log "Test 11: is_valid_ip_or_cidr_list rejects a newline between two valid tokens"

    local hostile
    hostile=$(printf '10.0.0.0/8\n192.168.1.1')
    if is_valid_ip_or_cidr_list "$hostile"; then
        fail "is_valid_ip_or_cidr_list accepted a newline between two otherwise-valid IP/CIDR tokens: $hostile"
    fi
    log "  ✓ newline-between-valid-tokens payload rejected"

    if (
        HTTP_PORT=8080 \
        INSTALL_DIR="${TEST_DIR}/install_tp_newline" DATA_DIR="${TEST_DIR}/install_tp_newline/data" \
        TRUSTED_PROXIES="$hostile" BIND_IP="" \
        validate_config
    ) >/dev/null 2>&1; then
        fail "validate_config accepted a newline-between-valid-tokens --trusted-proxies value"
    fi
    log "  ✓ validate_config rejects it end to end"

    # Exploitability check: even without validation, demonstrate why this
    # matters -- a newline in TRUSTED_PROXIES corrupts the .env round trip
    # (read_env_file() is line-based, so everything after the first
    # embedded newline in a value becomes a separate, dropped "line").
    local env_test_dir="${TEST_DIR}/tp_newline_env"
    mkdir -p "${env_test_dir}/etc"
    (
        INSTALL_DIR="$env_test_dir" DATA_DIR="${env_test_dir}/data" \
        HTTP_PORT=8080 TRUSTED_PROXIES="$hostile" BIND_IP="" MAGPIE_VERSION="test" \
        generate_env_file
    ) >/dev/null 2>&1

    local parsed_tp
    parsed_tp=$(
        declare -A parsed=()
        read_env_file "${env_test_dir}/etc/.env" parsed
        echo "${parsed[MAGPIE_TRUSTED_PROXIES]:-}"
    )
    if [[ "$parsed_tp" == *$'\n'* ]] || [[ "$parsed_tp" == *"192.168.1.1"* ]]; then
        fail "unexpected: newline survived the .env round trip intact: $parsed_tp"
    fi
    log "  ✓ (pre-validation-fix scenario) demonstrated: a newline in TRUSTED_PROXIES" \
        "silently truncates the value on .env round trip -- config-integrity bug, not RCE," \
        "now prevented entirely by rejecting the newline up front"
}

# Test 12: a horizontal tab is a legitimate whitespace separator for
# --trusted-proxies (tokenization already splits on it via IFS) and must
# not be rejected by the control-character guard -- only line breaks
# (which corrupt the .env round trip) should be.
test_trusted_proxies_allows_tab_separator() {
    log "Test 12: is_valid_ip_or_cidr_list accepts a tab-separated list"

    local tab_list
    tab_list=$(printf '10.0.0.0/8\t192.168.1.1')
    if ! is_valid_ip_or_cidr_list "$tab_list"; then
        fail "is_valid_ip_or_cidr_list rejected a legitimate tab-separated CIDR list (functional regression)"
    fi
    log "  ✓ tab-separated list accepted"

    local nl_list
    nl_list=$(printf '10.0.0.0/8\n192.168.1.1')
    if is_valid_ip_or_cidr_list "$nl_list"; then
        fail "is_valid_ip_or_cidr_list accepted a newline-separated list (regression of the #448 fix)"
    fi
    log "  ✓ newline-separated list is still rejected"
}

# Test 13: the "no source .env" assertion (Test 5, above) must actually
# detect a reintroduced `source`/`.` of the .env file, not just an
# anchored-at-column-0 pattern that never matches because
# magpie-deploy.sh's only literal `source` invocation
# (`source /etc/os-release` in check_os()) is indented. Verify the
# assertion's regex against a temporary copy with a source line
# reintroduced, so this specific test can't quietly go vacuous again.
test_source_env_detection_is_not_vacuous() {
    log "Test 13: the .env-not-sourced assertion actually detects a reintroduced source line"

    local pattern='(^|[[:space:]])(source|\.)[[:space:]]+.*etc/\.env'

    if grep -qE "$pattern" "$DEPLOY_SCRIPT"; then
        fail "magpie-deploy.sh currently sources the generated .env file -- investigate before trusting this test"
    fi
    log "  ✓ current magpie-deploy.sh does not match the source-detection pattern (expected)"

    local reintroduced="${TEST_DIR}/magpie-deploy-with-source-reintroduced.sh"
    {
        cat "$DEPLOY_SCRIPT"
        echo '    source "${INSTALL_DIR}/etc/.env"  # test-only: simulates a reintroduced vulnerability'
    } > "$reintroduced"

    if ! grep -qE "$pattern" "$reintroduced"; then
        fail "the source-detection pattern failed to catch a reintroduced (indented) source of etc/.env -- this assertion would be vacuous again"
    fi
    log "  ✓ pattern correctly detects an indented, reintroduced source of etc/.env"
}

# Test 14: read_env_file() must strip a trailing CR so a CRLF-terminated
# (e.g. Windows-edited) .env doesn't leave a stray \r embedded in parsed
# values, which downstream validators would then reject as a control
# character even though the value looks correct.
test_read_env_file_strips_crlf() {
    log "Test 14: read_env_file strips trailing CR from CRLF-terminated lines"

    local crlf_env="${TEST_DIR}/crlf.env"
    printf 'MAGPIE_DATA_DIR=/opt/magpie/data\r\nTRUSTED_PROXIES=10.0.0.0/8\r\nMAGPIE_IMAGE=ghcr.io/southwestccdc/magpie:test\r\n' > "$crlf_env"

    declare -A parsed=()
    read_env_file "$crlf_env" parsed

    if [[ "${parsed[TRUSTED_PROXIES]}" == *$'\r'* ]]; then
        fail "read_env_file left a trailing CR in TRUSTED_PROXIES: $(printf '%q' "${parsed[TRUSTED_PROXIES]}")"
    fi
    if [[ "${parsed[TRUSTED_PROXIES]}" != "10.0.0.0/8" ]]; then
        fail "read_env_file did not parse the CRLF-terminated value correctly: $(printf '%q' "${parsed[TRUSTED_PROXIES]}")"
    fi
    log "  ✓ CRLF-terminated value parsed without a stray trailing CR"

    if ! is_valid_ip_or_cidr_list "${parsed[TRUSTED_PROXIES]}"; then
        fail "the CR-stripped TRUSTED_PROXIES value unexpectedly still fails validation"
    fi
    log "  ✓ CR-stripped value passes downstream validation (previously would have failed on the stray control char)"

    if [[ "${parsed[MAGPIE_IMAGE]}" != "ghcr.io/southwestccdc/magpie:test" ]]; then
        fail "read_env_file did not parse the last CRLF-terminated value correctly: $(printf '%q' "${parsed[MAGPIE_IMAGE]}")"
    fi
    log "  ✓ last line of a CRLF file also parsed correctly (no CR left even without a trailing LF-only read)"
}

# Run all tests
log "Running tests for issue #448"
log ""

test_trusted_proxies_rejects_injection
test_validate_config_rejects_hostile_trusted_proxies
test_bind_ip_validation
test_env_file_not_sourced
test_load_existing_config_no_execution
test_path_validation
test_logs_lines_validation
test_uninstall_purge_revalidates_data_dir
test_update_rejects_hostile_install_dir
test_cidr_leading_zero_octets
test_trusted_proxies_rejects_newline_between_valid_tokens
test_trusted_proxies_allows_tab_separator
test_source_env_detection_is_not_vacuous
test_read_env_file_strips_crlf

log ""
log "All tests passed!"
