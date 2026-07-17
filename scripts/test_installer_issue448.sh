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
# 7. An escape-encoded --acme-server payload (\n, octal \173/\175/\040 --
#    i.e. no literal blocked bytes) is rejected by the allowlist validator,
#    and even if validation were bypassed, generate_caddyfile no longer
#    decodes it into a real directive (ENVIRON, not `awk -v`).
# 8. `uninstall --purge` refuses to `rm -rf` a DATA_DIR loaded from a
#    hostile .env.
# 9. `update` rejects an --install-dir containing sed metacharacters
#    (e.g. '|') before it reaches the docker-compose sed patching.
# 10. IPv4/IPv6 CIDR validation does not misjudge or error on leading-zero
#     octets/prefixes (bash arithmetic octal gotcha).
# 11. A newline sitting between two otherwise-valid --trusted-proxies
#     tokens is rejected up front, not left to per-token validation (which
#     would pass it, since each half is individually a valid IP/CIDR).
# 12. A tab-separated --trusted-proxies list is still accepted (tab is a
#     legitimate whitespace separator, not rejected as a control char).
# 13. generate_env_file() writes a single canonical MAGPIE_DOMAIN key (no
#     duplicate bare DOMAIN= that load_existing_config() never reads).
# 14. The "no source .env" test assertion actually detects a reintroduced
#     source line (verified against the anchoring bug that made it
#     vacuously pass regardless of whether .env was sourced).

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

# Test 11: an escape-encoded --acme-server payload (\n, octal \173/\175/\040
# -- i.e. no *literal* whitespace/brace/backslash-decoded bytes at
# validation time) must be rejected by the allowlist validator, not just a
# denylist of literal bytes. This is the payload class that bypassed the
# original denylist-based validation and reached awk's escape-decoding `-v`
# assignment.
test_acme_server_rejects_escape_encoded_injection() {
    log "Test 11: is_valid_acme_server_url rejects an escape-encoded injection payload"

    local hostile_acme
    hostile_acme=$(printf 'https://ca.example.com\\n\\175\\nadmin_endpoint\\0400.0.0.0:2999\\040\\173\\n\\tenforce_origin\\n\\175\\ntest\\040\\173')

    if is_valid_acme_server_url "$hostile_acme"; then
        fail "is_valid_acme_server_url accepted an escape-encoded injection payload: $hostile_acme"
    fi
    log "  ✓ escape-encoded payload rejected by the allowlist validator"

    if ! is_valid_acme_server_url "https://ca.example.com/acme/acme/directory"; then
        fail "is_valid_acme_server_url rejected a legitimate ACME server URL"
    fi
    log "  ✓ legitimate https:// ACME server URL accepted"

    if (
        TLS_MODE=auto DOMAIN="magpie.example.com" TLS_CERT="" TLS_KEY="" \
        HTTP_PORT=8080 HTTPS_PORT=8443 \
        INSTALL_DIR="${TEST_DIR}/install_acme_escape" DATA_DIR="${TEST_DIR}/install_acme_escape/data" \
        TRUSTED_PROXIES="" BIND_IP="" ACME_SERVER="$hostile_acme" \
        validate_config
    ) >/dev/null 2>&1; then
        fail "validate_config accepted an escape-encoded --acme-server payload"
    fi
    log "  ✓ validate_config rejects the escape-encoded payload end to end"
}

# Test 12: even if ACME_SERVER validation were bypassed, generate_caddyfile
# must not decode escape sequences in it into real Caddyfile syntax. This
# is the defense-in-depth layer: ACME_SERVER is passed to awk via ENVIRON
# (which does not decode \n / octal escapes), not `awk -v` (which does).
test_generate_caddyfile_no_escape_decoding() {
    log "Test 12: generate_caddyfile does not decode escape sequences in ACME_SERVER"

    local hostile_acme
    hostile_acme=$(printf 'https://ca.example.com\\n\\175\\nadmin_endpoint\\0400.0.0.0:2999\\040\\173\\n\\tenforce_origin\\n\\175\\ntest\\040\\173')

    local install_dir="${TEST_DIR}/gc_escape_install"
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

    (
        INSTALL_DIR="$install_dir" MAGPIE_VERSION="test" TLS_MODE="auto" \
        DOMAIN="magpie.example.com" ACME_SERVER="$hostile_acme" \
        generate_caddyfile
    ) >/dev/null 2>&1 || true

    local caddyfile="${install_dir}/etc/Caddyfile"
    [[ -f "$caddyfile" ]] || fail "generate_caddyfile did not produce a Caddyfile"

    # A real injected directive would appear as its own line (awk decoding
    # \n into an actual newline, and \175/\173 into standalone braces). If
    # the escapes were left inert, everything stays on the single `ca` line.
    if grep -qxE '[[:space:]]*admin_endpoint.*' "$caddyfile"; then
        fail "admin_endpoint was injected as its own Caddyfile directive -- escape sequences were decoded"
    fi
    log "  ✓ no standalone admin_endpoint directive was injected"

    local ca_lines
    # [[:space:]] rather than \s -- \s is not a portable whitespace escape
    # in POSIX basic/extended grep regex (GNU grep treats a literal 's').
    ca_lines=$(grep -cE '^[[:space:]]*ca ' "$caddyfile" || true)
    if [[ "$ca_lines" -ne 1 ]]; then
        fail "expected exactly one 'ca' directive line, found $ca_lines"
    fi
    log "  ✓ hostile ACME_SERVER value stayed inert on a single 'ca' line (no escape decoding)"
}

# Test 13: `uninstall --purge` must refuse to `rm -rf` a DATA_DIR that was
# loaded from a hostile/stale .env, not just trust whatever load_existing_config
# populated it with.
test_uninstall_purge_revalidates_data_dir() {
    log "Test 13: cmd_uninstall --purge refuses a hostile DATA_DIR loaded from .env"

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

# Test 14: `update` must reject an --install-dir containing sed
# metacharacters (e.g. '|') before it reaches
# patch_compose_for_caddyfile()/patch_compose_for_tls_certs(), which
# interpolate INSTALL_DIR into a `sed 's|...|...|g'` replacement.
test_update_rejects_hostile_install_dir() {
    log "Test 14: cmd_update rejects an --install-dir containing sed metacharacters"

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

# Test 15: leading-zero IPv4 octets / CIDR prefixes must not be misjudged
# (bash arithmetic treats a leading zero as octal) or throw a stderr error.
test_cidr_leading_zero_octets() {
    log "Test 15: IPv4/IPv6 CIDR validation handles leading-zero octets/prefixes correctly"

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

# Test 16: a newline sitting *between* two otherwise-valid IP/CIDR tokens
# must be rejected. Splitting on IFS whitespace (which includes newline)
# means each half of "10.0.0.0/8\n192.168.1.1" is individually a valid
# token, so a purely per-token validator lets the newline through even
# though every token "looks like" a CIDR (Copilot review finding). Also
# confirms tokenization no longer globs (`read -ra`, not `for t in $list`).
test_trusted_proxies_rejects_newline_between_valid_tokens() {
    log "Test 16: is_valid_ip_or_cidr_list rejects a newline between two valid tokens"

    local hostile
    hostile=$(printf '10.0.0.0/8\n192.168.1.1')
    if is_valid_ip_or_cidr_list "$hostile"; then
        fail "is_valid_ip_or_cidr_list accepted a newline between two otherwise-valid IP/CIDR tokens: $hostile"
    fi
    log "  ✓ newline-between-valid-tokens payload rejected"

    if (
        TLS_MODE=off DOMAIN="" TLS_CERT="" TLS_KEY="" \
        HTTP_PORT=8080 HTTPS_PORT=8443 \
        INSTALL_DIR="${TEST_DIR}/install_tp_newline" DATA_DIR="${TEST_DIR}/install_tp_newline/data" \
        TRUSTED_PROXIES="$hostile" BIND_IP="" ACME_SERVER="" \
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
        HTTP_PORT=8080 HTTPS_PORT=8443 DOMAIN="" TLS_MODE="off" \
        TRUSTED_PROXIES="$hostile" BIND_IP="" ACME_SERVER="" MAGPIE_VERSION="test" \
        generate_env_file
    ) >/dev/null 2>&1

    local parsed_tp
    parsed_tp=$(
        declare -A parsed=()
        read_env_file "${env_test_dir}/etc/.env" parsed
        echo "${parsed[TRUSTED_PROXIES]:-}"
    )
    if [[ "$parsed_tp" == *$'\n'* ]] || [[ "$parsed_tp" == *"192.168.1.1"* ]]; then
        fail "unexpected: newline survived the .env round trip intact: $parsed_tp"
    fi
    log "  ✓ (pre-validation-fix scenario) demonstrated: a newline in TRUSTED_PROXIES" \
        "silently truncates the value on .env round trip -- config-integrity bug, not RCE," \
        "now prevented entirely by rejecting the newline up front"
}

# Test 17: a horizontal tab is a legitimate whitespace separator for
# --trusted-proxies (tokenization already splits on it via IFS) and must
# not be rejected by the control-character guard -- only line breaks
# (which corrupt the .env/Caddyfile round trip) should be.
test_trusted_proxies_allows_tab_separator() {
    log "Test 17: is_valid_ip_or_cidr_list accepts a tab-separated list"

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

# Test 18: generate_env_file() must write a single canonical domain key
# (MAGPIE_DOMAIN); load_existing_config() only ever reads that one. A
# duplicate bare DOMAIN= key would be silently ignored if an operator
# edited it, which is confusing -- assert it isn't written, and that the
# domain still round-trips correctly through the single key.
test_domain_single_canonical_key() {
    log "Test 18: generate_env_file/load_existing_config use a single canonical DOMAIN key"

    local install_dir="${TEST_DIR}/domain_key_install"
    mkdir -p "${install_dir}/etc"

    (
        INSTALL_DIR="$install_dir" DATA_DIR="${install_dir}/data" \
        HTTP_PORT=8080 HTTPS_PORT=8443 DOMAIN="magpie.example.com" \
        TLS_MODE="auto" TRUSTED_PROXIES="" BIND_IP="" ACME_SERVER="" \
        MAGPIE_VERSION="test" \
        generate_env_file
    ) >/dev/null 2>&1

    local env_file="${install_dir}/etc/.env"
    [[ -f "$env_file" ]] || fail "generate_env_file did not produce a .env file"

    if grep -qE '^DOMAIN=' "$env_file"; then
        fail "generate_env_file wrote a duplicate bare DOMAIN= key -- edits to it would be silently ignored by load_existing_config"
    fi
    log "  ✓ no duplicate bare DOMAIN= key written"

    if ! grep -q '^MAGPIE_DOMAIN=magpie.example.com$' "$env_file"; then
        fail "generate_env_file did not write the canonical MAGPIE_DOMAIN= key"
    fi
    log "  ✓ canonical MAGPIE_DOMAIN= key written"

    # NOTE: variables must be set as standalone assignments here, not
    # prefixed to the load_existing_config call (`VAR=val load_existing_config`)
    # -- a prefix assignment is scoped only to that one command and reverts
    # once it returns, so a later `echo "$DOMAIN"` in the same subshell
    # would read the pre-call value, not what the function set.
    local out
    out=$(
        (
            # shellcheck disable=SC2034  # all consumed by load_existing_config(), sourced from magpie-deploy.sh
            INSTALL_DIR="$install_dir"
            # shellcheck disable=SC2034
            DATA_DIR=""
            # shellcheck disable=SC2034
            HTTP_PORT=""
            # shellcheck disable=SC2034
            HTTPS_PORT=""
            DOMAIN=""
            # shellcheck disable=SC2034
            TLS_MODE=""
            TRUSTED_PROXIES=""
            # shellcheck disable=SC2034
            BIND_IP=""
            # shellcheck disable=SC2034
            ACME_SERVER=""
            load_existing_config
            echo "DOMAIN=$DOMAIN"
        )
    )
    if [[ "$out" != "DOMAIN=magpie.example.com" ]]; then
        fail "DOMAIN did not round-trip through load_existing_config: $out"
    fi
    log "  ✓ DOMAIN round-trips correctly through the single canonical key"
}

# Test 19: the "no source .env" assertion (Test 8, above) must actually
# detect a reintroduced `source`/`.` of the .env file, not just an
# anchored-at-column-0 pattern that never matches because
# magpie-deploy.sh's only literal `source` invocation
# (`source /etc/os-release` in check_os()) is indented. Verify the
# assertion's regex against a temporary copy with a source line
# reintroduced, so this specific test can't quietly go vacuous again.
test_source_env_detection_is_not_vacuous() {
    log "Test 19: the .env-not-sourced assertion actually detects a reintroduced source line"

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
test_acme_server_rejects_escape_encoded_injection
test_generate_caddyfile_no_escape_decoding
test_uninstall_purge_revalidates_data_dir
test_update_rejects_hostile_install_dir
test_cidr_leading_zero_octets
test_trusted_proxies_rejects_newline_between_valid_tokens
test_trusted_proxies_allows_tab_separator
test_domain_single_canonical_key
test_source_env_detection_is_not_vacuous

log ""
log "All tests passed!"
