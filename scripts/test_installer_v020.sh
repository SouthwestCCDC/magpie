#!/bin/bash
# Test script for the v0.2.0 installer rewrite (PR-3): the canonical
# single-service compose, TLS deprecation, and the #583/#579 fixes.
#
# This script exercises the *real* functions from magpie-deploy.sh
# (sourced, not reimplemented) to verify that:
# 1. generate_env_file() writes MAGPIE_IMAGE=<ver>-bundled,
#    MAGPIE_ADMIN_TOKEN_SINK=file (+_FILE_PATH), and MAGPIE_BIND_IP -- and
#    does NOT write MAGPIE_DOMAIN, MAGPIE_HTTPS_PORT, TLS_MODE, or
#    ACME_SERVER (the fail-closed admin-token-sink boot-loop guard and the
#    dead TLS keys).
# 2. Tier-1 deprecation: parse_args accepts and warns on each of the six
#    deprecated flags (--tls-mode, --domain, --tls-cert, --tls-key,
#    --https-port, --acme-server) without dying.
# 3. reconcile_env_file_for_update() (cmd_update's .env surgery, factored
#    out for testability): strips the dead TLS keys, adds a missing
#    admin-token-sink without clobbering an existing one, and upserts
#    MAGPIE_BIND_IP.
# 4. Tier-2 gate: cmd_update refuses an install with a persisted
#    TLS_MODE=auto|manual without --accept-builtin-tls-removed, and
#    proceeds past the gate (reaching the next real failure, the missing
#    repo dir) with it.
# 5. Issue #583: reconcile_env_file_for_update() changes an
#    already-persisted (including explicitly empty) MAGPIE_TRUSTED_PROXIES
#    in place when --trusted-proxies was passed on this run.
# 6. The collapsed issue #579 gate: cmd_install's fresh-install path warns
#    (never dies) under --noninteractive when MAGPIE_ALLOWED_CIDRS is real
#    and MAGPIE_TRUSTED_PROXIES is empty; cmd_update still hard-fails the
#    same condition without --accept-empty-trusted-proxies.
# 7. upsert_env_key()/strip_env_key() themselves: idempotent add-or-replace
#    and remove-if-present against an anchored key.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_SCRIPT="${SCRIPT_DIR}/magpie-deploy.sh"
TEST_DIR=$(mktemp -d)

cleanup() {
    rm -rf "$TEST_DIR"
}
trap cleanup EXIT

# shellcheck source=/dev/null
source <(sed '/^main "\$@"$/d' "$DEPLOY_SCRIPT")

# See test_installer_issue448.sh for why this is re-asserted after sourcing.
set +e
set -uo pipefail

log() {
    echo "[test] $*"
}

fail() {
    echo "[test] FAIL: $*" >&2
    exit 1
}

# Test 1: generate_env_file() writes the v0.2.0 keys and omits the dead
# TLS keys.
test_generate_env_file_v020_keys() {
    log "Test 1: generate_env_file() writes MAGPIE_IMAGE/MAGPIE_ADMIN_TOKEN_SINK/MAGPIE_BIND_IP, omits dead TLS keys"

    local install_dir="${TEST_DIR}/gen_env_install"
    mkdir -p "${install_dir}/etc"

    (
        INSTALL_DIR="$install_dir" DATA_DIR="${install_dir}/data" \
        HTTP_PORT=8080 TRUSTED_PROXIES="" BIND_IP="10.3.3.107" \
        MAGPIE_VERSION="0.2.0" \
        generate_env_file
    ) >/dev/null 2>&1

    local env_file="${install_dir}/etc/.env"
    [[ -f "$env_file" ]] || fail "generate_env_file did not produce a .env file"

    if ! grep -q '^MAGPIE_IMAGE=ghcr.io/southwestccdc/magpie:0.2.0-bundled$' "$env_file"; then
        fail "generate_env_file did not pin MAGPIE_IMAGE to the -bundled tag: $(grep MAGPIE_IMAGE "$env_file")"
    fi
    log "  ✓ MAGPIE_IMAGE pinned to the -bundled tag"

    if ! grep -q '^MAGPIE_ADMIN_TOKEN_SINK=file$' "$env_file"; then
        fail "generate_env_file did not write MAGPIE_ADMIN_TOKEN_SINK=file -- the canonical compose is fail-closed on this and would boot-loop"
    fi
    if ! grep -q '^MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH=/data/admin-token$' "$env_file"; then
        fail "generate_env_file did not write MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH"
    fi
    log "  ✓ MAGPIE_ADMIN_TOKEN_SINK(+_FILE_PATH) written -- boot-loop guard in place"

    if ! grep -q '^MAGPIE_BIND_IP=10.3.3.107$' "$env_file"; then
        fail "generate_env_file did not write MAGPIE_BIND_IP: $(grep MAGPIE_BIND_IP "$env_file")"
    fi
    log "  ✓ MAGPIE_BIND_IP written"

    local dead_key
    for dead_key in MAGPIE_DOMAIN MAGPIE_HTTPS_PORT TLS_MODE ACME_SERVER; do
        if grep -q "^${dead_key}=" "$env_file"; then
            fail "generate_env_file wrote a dead key that the canonical compose no longer reads: ${dead_key}"
        fi
    done
    log "  ✓ no dead TLS keys (MAGPIE_DOMAIN/MAGPIE_HTTPS_PORT/TLS_MODE/ACME_SERVER) written"
}

# Test 2: Tier-1 deprecation -- parse_args accepts and warns on each of the
# six deprecated flags without dying. Uses the 'status' command (cheapest
# no-op path once past parsing) against a non-existent install dir so it
# fails fast on verify_installation, well after parse_args has already
# accepted the flag -- the assertion is about the flag not being "Unknown
# option", not about status succeeding.
test_tier1_deprecated_flags_accepted_and_warn() {
    log "Test 2: Tier-1 deprecation -- six deprecated flags parse (warn, no-op) instead of erroring"

    local flag value
    while IFS=' ' read -r flag value; do
        local out rc
        out=$(bash "$DEPLOY_SCRIPT" status --install-dir "${TEST_DIR}/nonexistent" "$flag" "$value" 2>&1)
        rc=$?

        if echo "$out" | grep -q "Unknown option"; then
            fail "$flag was rejected as an unknown option -- Tier-1 deprecation must still accept it: $out"
        fi
        if ! echo "$out" | grep -qi "deprecated"; then
            fail "$flag did not print a deprecation warning: $out"
        fi
        # verify_installation() is expected to fail (no such install) --
        # that's just proof parsing got past the flag, not a real check.
        if [[ $rc -eq 0 ]]; then
            fail "$flag: expected the nonexistent-install-dir status check to fail, got success (test setup problem)"
        fi
        log "  ✓ $flag: accepted, warned, no-op"
    done << 'EOF'
--tls-mode off
--tls-mode auto
--domain magpie.example.com
--tls-cert /tmp/cert.pem
--tls-key /tmp/key.pem
--https-port 8443
--acme-server https://ca.example.com/acme/acme/directory
EOF
}

# Test 3: reconcile_env_file_for_update() strips dead keys, adds a missing
# admin-token-sink without clobbering an existing one, and upserts
# MAGPIE_BIND_IP.
test_reconcile_env_file_strips_and_adds() {
    log "Test 3: reconcile_env_file_for_update() strips dead keys, adds missing admin-token-sink, upserts MAGPIE_BIND_IP"

    local env_file="${TEST_DIR}/reconcile1.env"
    cat > "$env_file" << 'EOF'
MAGPIE_DATA_DIR=/opt/magpie/data
MAGPIE_HTTP_PORT=8080
MAGPIE_DOMAIN=magpie.example.com
MAGPIE_HTTPS_PORT=8443
TLS_MODE=off
ACME_SERVER=
EOF

    (
        TRUSTED_PROXIES="" BIND_IP="" TRUSTED_PROXIES_FROM_CLI="false" \
        reconcile_env_file_for_update "$env_file"
    ) >/dev/null 2>&1

    local dead_key
    for dead_key in MAGPIE_DOMAIN MAGPIE_HTTPS_PORT TLS_MODE ACME_SERVER; do
        if grep -q "^${dead_key}=" "$env_file"; then
            fail "reconcile_env_file_for_update did not strip dead key: ${dead_key}"
        fi
    done
    log "  ✓ dead TLS keys stripped"

    if ! grep -q '^MAGPIE_ADMIN_TOKEN_SINK=file$' "$env_file"; then
        fail "reconcile_env_file_for_update did not add a missing MAGPIE_ADMIN_TOKEN_SINK"
    fi
    if ! grep -q '^MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH=/data/admin-token$' "$env_file"; then
        fail "reconcile_env_file_for_update did not add a missing MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH"
    fi
    log "  ✓ MAGPIE_ADMIN_TOKEN_SINK(+_FILE_PATH) added -- update-path boot-loop guard in place"

    if ! grep -q '^MAGPIE_BIND_IP=$' "$env_file"; then
        fail "reconcile_env_file_for_update did not upsert MAGPIE_BIND_IP: $(grep MAGPIE_BIND_IP "$env_file")"
    fi
    log "  ✓ MAGPIE_BIND_IP upserted (empty -- no --bind-ip / legacy key on this run)"

    # Never clobber an existing operator choice (e.g. exec/discard).
    local env_file2="${TEST_DIR}/reconcile2.env"
    cat > "$env_file2" << 'EOF'
MAGPIE_DATA_DIR=/opt/magpie/data
MAGPIE_ADMIN_TOKEN_SINK=discard
EOF
    (
        TRUSTED_PROXIES="" BIND_IP="" TRUSTED_PROXIES_FROM_CLI="false" \
        reconcile_env_file_for_update "$env_file2"
    ) >/dev/null 2>&1
    if ! grep -q '^MAGPIE_ADMIN_TOKEN_SINK=discard$' "$env_file2"; then
        fail "reconcile_env_file_for_update clobbered an operator's existing MAGPIE_ADMIN_TOKEN_SINK=discard: $(grep MAGPIE_ADMIN_TOKEN_SINK "$env_file2")"
    fi
    log "  ✓ an already-configured MAGPIE_ADMIN_TOKEN_SINK is never clobbered"
}

# Test 4: BIND_IP migration -- a legacy unprefixed BIND_IP key is migrated
# to MAGPIE_BIND_IP and the legacy key is dropped.
test_reconcile_env_file_migrates_legacy_bind_ip() {
    log "Test 4: reconcile_env_file_for_update() migrates legacy BIND_IP to MAGPIE_BIND_IP"

    local install_dir="${TEST_DIR}/bindip_install"
    mkdir -p "${install_dir}/etc"
    local env_file="${install_dir}/etc/.env"
    cat > "$env_file" << 'EOF'
MAGPIE_DATA_DIR=/opt/magpie/data
BIND_IP=10.3.3.107
EOF

    # load_existing_config() populates BIND_IP from the legacy key when
    # MAGPIE_BIND_IP is absent -- reconcile_env_file_for_update() then
    # writes that resolved value forward and drops the legacy key.
    #
    # NOTE: standalone assignments, not prefixed to the load_existing_config
    # call -- a prefix assignment (`VAR=val some_function`) is scoped only
    # to that one command and reverts once it returns, so a later read of
    # $BIND_IP in this same subshell would see the pre-call value, not what
    # the function set internally. See test_installer_issue448.sh's
    # test_domain_single_canonical_key-derived lesson (same footgun).
    (
        # shellcheck disable=SC2034  # all consumed by load_existing_config()/reconcile_env_file_for_update(), sourced from magpie-deploy.sh
        INSTALL_DIR="$install_dir"
        # shellcheck disable=SC2034
        DATA_DIR=""
        # shellcheck disable=SC2034
        HTTP_PORT=""
        # shellcheck disable=SC2034  # both read by reconcile_env_file_for_update below, after load_existing_config reassigns them
        TRUSTED_PROXIES=""
        # shellcheck disable=SC2034
        BIND_IP=""
        # shellcheck disable=SC2034
        TRUSTED_PROXIES_FROM_CLI="false"
        load_existing_config
        reconcile_env_file_for_update "$env_file"
    ) >/dev/null 2>&1

    if ! grep -q '^MAGPIE_BIND_IP=10.3.3.107$' "$env_file"; then
        fail "legacy BIND_IP was not migrated to MAGPIE_BIND_IP: $(grep BIND_IP "$env_file")"
    fi
    if grep -q '^BIND_IP=' "$env_file"; then
        fail "legacy unprefixed BIND_IP key was not removed after migration"
    fi
    log "  ✓ legacy BIND_IP migrated to MAGPIE_BIND_IP and the old key removed"
}

# Test 5: issue #583 -- an explicit --trusted-proxies changes an
# already-persisted (including explicitly empty) MAGPIE_TRUSTED_PROXIES in
# place, which previously was a no-op (the bug this fixes).
test_583_trusted_proxies_upsert_in_place() {
    log "Test 5: reconcile_env_file_for_update() changes an already-persisted MAGPIE_TRUSTED_PROXIES in place (#583)"

    local env_file="${TEST_DIR}/583.env"
    cat > "$env_file" << 'EOF'
MAGPIE_DATA_DIR=/opt/magpie/data
MAGPIE_TRUSTED_PROXIES=
EOF

    (
        TRUSTED_PROXIES="172.20.0.0/16" BIND_IP="" TRUSTED_PROXIES_FROM_CLI="true" \
        reconcile_env_file_for_update "$env_file"
    ) >/dev/null 2>&1

    if ! grep -q '^MAGPIE_TRUSTED_PROXIES=172.20.0.0/16$' "$env_file"; then
        fail "an explicit --trusted-proxies did not update an already-persisted (empty) MAGPIE_TRUSTED_PROXIES -- issue #583 regression: $(grep MAGPIE_TRUSTED_PROXIES "$env_file")"
    fi
    log "  ✓ already-persisted MAGPIE_TRUSTED_PROXIES updated in place by an explicit --trusted-proxies"

    # Without TRUSTED_PROXIES_FROM_CLI, an already-present key is left
    # alone (the pre-existing, still-correct migrate-if-absent behavior).
    local env_file2="${TEST_DIR}/583b.env"
    cat > "$env_file2" << 'EOF'
MAGPIE_DATA_DIR=/opt/magpie/data
MAGPIE_TRUSTED_PROXIES=10.0.0.0/8
EOF
    (
        TRUSTED_PROXIES="10.0.0.0/8" BIND_IP="" TRUSTED_PROXIES_FROM_CLI="false" \
        reconcile_env_file_for_update "$env_file2"
    ) >/dev/null 2>&1
    if ! grep -q '^MAGPIE_TRUSTED_PROXIES=10.0.0.0/8$' "$env_file2"; then
        fail "an already-present MAGPIE_TRUSTED_PROXIES was unexpectedly changed without --trusted-proxies: $(grep MAGPIE_TRUSTED_PROXIES "$env_file2")"
    fi
    log "  ✓ an already-present value is left alone when --trusted-proxies was not passed this run"
}

# Test 6: tier-2 gate -- cmd_update refuses an install with a persisted
# TLS_MODE=auto|manual without --accept-builtin-tls-removed, and gets past
# the gate (reaching the next real failure -- the missing repo dir, since
# this test never sets up a real git clone) with it.
test_tier2_gate_blocks_and_bypasses() {
    log "Test 6: cmd_update tier-2 gate on persisted TLS_MODE=auto|manual"

    local install_dir="${TEST_DIR}/tier2_install"
    mkdir -p "${install_dir}/etc"
    cat > "${install_dir}/etc/.env" << 'EOF'
MAGPIE_DATA_DIR=/opt/magpie/data
MAGPIE_HTTP_PORT=8080
TLS_MODE=auto
EOF

    local out
    if out=$(
        (
            INSTALL_DIR="$install_dir" NONINTERACTIVE="true" cmd_update
        ) 2>&1
    ); then
        fail "cmd_update proceeded past the tier-2 gate without --accept-builtin-tls-removed"
    fi
    if ! echo "$out" | grep -q "accept-builtin-tls-removed"; then
        fail "cmd_update's tier-2 gate failure did not mention the bypass flag: $out"
    fi
    log "  ✓ cmd_update refuses to proceed on a persisted TLS_MODE=auto without --accept-builtin-tls-removed"

    if out=$(
        (
            INSTALL_DIR="$install_dir" NONINTERACTIVE="true" \
            ACCEPT_BUILTIN_TLS_REMOVED="true" ACCEPT_EMPTY_TRUSTED_PROXIES="true" \
            cmd_update
        ) 2>&1
    ); then
        fail "cmd_update unexpectedly succeeded (no repo dir was set up -- test setup problem)"
    fi
    if echo "$out" | grep -qi "tier-2\|terminating TLS itself"; then
        fail "cmd_update still hit the tier-2 gate even with --accept-builtin-tls-removed: $out"
    fi
    if ! echo "$out" | grep -q "Repository directory not found"; then
        fail "expected cmd_update to get past the tier-2 gate and fail on the missing repo dir instead: $out"
    fi
    log "  ✓ --accept-builtin-tls-removed gets past the gate (next failure is the expected missing-repo-dir one)"

    # A persisted TLS_MODE=off (already HTTP-only) never gates.
    local install_dir_off="${TEST_DIR}/tier2_off_install"
    mkdir -p "${install_dir_off}/etc"
    cat > "${install_dir_off}/etc/.env" << 'EOF'
MAGPIE_DATA_DIR=/opt/magpie/data
TLS_MODE=off
EOF
    out=$(
        (
            INSTALL_DIR="$install_dir_off" NONINTERACTIVE="true" \
            ACCEPT_EMPTY_TRUSTED_PROXIES="true" cmd_update
        ) 2>&1
    )
    if echo "$out" | grep -qi "terminating TLS itself"; then
        fail "cmd_update gated a persisted TLS_MODE=off install -- it was already HTTP-only, nothing changed"
    fi
    log "  ✓ a persisted TLS_MODE=off does not trigger the tier-2 gate"
}

# Test 7: the collapsed issue #579 gate -- cmd_install's fresh-install path
# warns (never dies) under --noninteractive; cmd_update still hard-fails
# the same condition without --accept-empty-trusted-proxies.
test_579_gate_install_warns_update_dies() {
    log "Test 7: collapsed #579 gate -- install warns, update hard-fails, both without --accept-empty-trusted-proxies"

    local out
    out=$(
        (
            ALLOWED_CIDRS="10.0.0.0/8" TRUSTED_PROXIES="" \
            TRUSTED_PROXIES_KEY_PRESENT="false" ACCEPT_EMPTY_TRUSTED_PROXIES="false" \
            NONINTERACTIVE="true" INSTALL_DIR="${TEST_DIR}/579_install" \
            warn_or_gate_trusted_proxies_for_cidr_allow "install"
        ) 2>&1
    )
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        fail "the #579 gate died on a fresh 'install' -- it must only warn (install has nothing running to lock anyone out of): $out"
    fi
    if ! echo "$out" | grep -qi "MAGPIE_ALLOWED_CIDRS"; then
        fail "the #579 gate produced no warning for install mode: $out"
    fi
    log "  ✓ install mode: warns, does not die"

    if out=$(
        (
            ALLOWED_CIDRS="10.0.0.0/8" TRUSTED_PROXIES="" \
            TRUSTED_PROXIES_KEY_PRESENT="false" ACCEPT_EMPTY_TRUSTED_PROXIES="false" \
            NONINTERACTIVE="true" INSTALL_DIR="${TEST_DIR}/579_update" \
            warn_or_gate_trusted_proxies_for_cidr_allow "update"
        ) 2>&1
    ); then
        fail "the #579 gate did not die on 'update' with the same unconfigured condition"
    fi
    if ! echo "$out" | grep -q "accept-empty-trusted-proxies"; then
        fail "the #579 gate's update failure did not mention the bypass flag: $out"
    fi
    log "  ✓ update mode: hard-fails (unattended-lockout protection for a running deployment)"
}

# Test 8: upsert_env_key()/strip_env_key() primitives.
test_upsert_and_strip_env_key_primitives() {
    log "Test 8: upsert_env_key()/strip_env_key() primitives"

    local env_file="${TEST_DIR}/primitives.env"
    cat > "$env_file" << 'EOF'
MAGPIE_DATA_DIR=/opt/magpie/data
MAGPIE_HTTP_PORT=8080
EOF

    upsert_env_key "$env_file" "MAGPIE_HTTP_PORT" "9090"
    if ! grep -qx 'MAGPIE_HTTP_PORT=9090' "$env_file"; then
        fail "upsert_env_key did not replace an existing key in place"
    fi
    if [[ $(grep -c '^MAGPIE_HTTP_PORT=' "$env_file") -ne 1 ]]; then
        fail "upsert_env_key produced a duplicate key instead of replacing in place"
    fi
    log "  ✓ upsert_env_key replaces an existing key in place (no duplicate)"

    upsert_env_key "$env_file" "MAGPIE_NEW_KEY" "hello"
    if ! grep -qx 'MAGPIE_NEW_KEY=hello' "$env_file"; then
        fail "upsert_env_key did not append a new key"
    fi
    log "  ✓ upsert_env_key appends an absent key"

    strip_env_key "$env_file" "MAGPIE_NEW_KEY"
    if grep -q '^MAGPIE_NEW_KEY=' "$env_file"; then
        fail "strip_env_key did not remove the key"
    fi
    log "  ✓ strip_env_key removes a present key"

    # No-op on an absent key -- must not error or touch the file otherwise.
    local before after
    before=$(md5sum "$env_file")
    strip_env_key "$env_file" "MAGPIE_NEVER_EXISTED"
    after=$(md5sum "$env_file")
    if [[ "$before" != "$after" ]]; then
        fail "strip_env_key modified the file for an absent key"
    fi
    log "  ✓ strip_env_key is a no-op for an absent key"

    # Anchoring: a key that is a substring of another key's name must not
    # be touched (^KEY= anchoring, not a bare substring match).
    local env_file2="${TEST_DIR}/anchor.env"
    cat > "$env_file2" << 'EOF'
MAGPIE_HTTP_PORT=8080
MAGPIE_HTTP_PORT_EXTRA=unrelated
EOF
    upsert_env_key "$env_file2" "MAGPIE_HTTP_PORT" "9090"
    if ! grep -qx 'MAGPIE_HTTP_PORT_EXTRA=unrelated' "$env_file2"; then
        fail "upsert_env_key touched an unrelated key that merely shares a prefix"
    fi
    log "  ✓ upsert_env_key is anchored -- does not touch a key that shares a name prefix"
}

# Run all tests
log "Running tests for the v0.2.0 installer rewrite (PR-3)"
log ""

test_generate_env_file_v020_keys
test_tier1_deprecated_flags_accepted_and_warn
test_reconcile_env_file_strips_and_adds
test_reconcile_env_file_migrates_legacy_bind_ip
test_583_trusted_proxies_upsert_in_place
test_tier2_gate_blocks_and_bypasses
test_579_gate_install_warns_update_dies
test_upsert_and_strip_env_key_primitives

log ""
log "All tests passed!"
