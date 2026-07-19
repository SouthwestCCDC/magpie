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

    # A PRESENT-BUT-EMPTY MAGPIE_ADMIN_TOKEN_SINK (e.g. a hand-edited
    # placeholder) is exactly as fail-closed as an absent one -- must be
    # treated as missing and fixed, not left alone by a bare presence
    # check. Same for a whitespace-only value.
    local env_file3="${TEST_DIR}/reconcile3.env"
    cat > "$env_file3" << 'EOF'
MAGPIE_DATA_DIR=/opt/magpie/data
MAGPIE_ADMIN_TOKEN_SINK=
MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH=
EOF
    (
        TRUSTED_PROXIES="" BIND_IP="" TRUSTED_PROXIES_FROM_CLI="false" \
        reconcile_env_file_for_update "$env_file3"
    ) >/dev/null 2>&1
    if ! grep -q '^MAGPIE_ADMIN_TOKEN_SINK=file$' "$env_file3"; then
        fail "reconcile_env_file_for_update left a present-but-empty MAGPIE_ADMIN_TOKEN_SINK unfixed -- boot-loop guard gap: $(grep MAGPIE_ADMIN_TOKEN_SINK= "$env_file3")"
    fi
    if ! grep -q '^MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH=/data/admin-token$' "$env_file3"; then
        fail "reconcile_env_file_for_update left a present-but-whitespace-only MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH unfixed: $(grep MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH "$env_file3")"
    fi
    log "  ✓ a present-but-empty/whitespace-only MAGPIE_ADMIN_TOKEN_SINK(+_FILE_PATH) is treated as missing and fixed"
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

# Test 5b (A1 review finding on PR #597): a CLI override must survive the
# REAL cmd_update() call order -- parse_args (sets the value + its
# *_FROM_CLI marker) THEN load_existing_config() THEN
# reconcile_env_file_for_update(). Every other test above calls
# reconcile_env_file_for_update() directly with a hand-set
# TRUSTED_PROXIES/BIND_IP, which bypasses load_existing_config() entirely
# -- exactly why the actual bug (load_existing_config() unconditionally
# overwriting both from .env, clobbering a CLI override that runs before
# it in real usage) slipped past the tests that motivated the #583 fix in
# the first place. This test exercises both functions together, for both
# variables, in both the absent-key and already-present-key (including
# empty) cases.
test_cli_override_survives_load_existing_config() {
    log "Test 5b: a CLI --trusted-proxies/--bind-ip override survives load_existing_config() before reconcile_env_file_for_update() runs"

    run_update_env_surgery() {
        local env_file="$1" trusted_proxies="$2" trusted_proxies_from_cli="$3" bind_ip="$4" bind_ip_from_cli="$5"
        local install_dir
        install_dir="$(dirname "$(dirname "$env_file")")"
        (
            # shellcheck disable=SC2034  # consumed by load_existing_config(), sourced from magpie-deploy.sh
            INSTALL_DIR="$install_dir"
            # shellcheck disable=SC2034
            DATA_DIR=""
            # shellcheck disable=SC2034
            HTTP_PORT=""
            # shellcheck disable=SC2034  # all four read by load_existing_config()/reconcile_env_file_for_update() below
            TRUSTED_PROXIES="$trusted_proxies"
            # shellcheck disable=SC2034
            TRUSTED_PROXIES_FROM_CLI="$trusted_proxies_from_cli"
            # shellcheck disable=SC2034
            BIND_IP="$bind_ip"
            # shellcheck disable=SC2034
            BIND_IP_FROM_CLI="$bind_ip_from_cli"
            load_existing_config
            reconcile_env_file_for_update "$env_file"
        ) >/dev/null 2>&1
    }

    # Case A: --trusted-proxies/--bind-ip against a .env with NO key present
    # at all (a pre-v0.2.0 install, or one that never touched these).
    local install_a="${TEST_DIR}/cli_override_a"
    mkdir -p "${install_a}/etc"
    local env_a="${install_a}/etc/.env"
    echo "MAGPIE_DATA_DIR=/opt/magpie/data" > "$env_a"
    run_update_env_surgery "$env_a" "172.20.0.0/16" "true" "10.3.3.107" "true"
    if ! grep -q '^MAGPIE_TRUSTED_PROXIES=172.20.0.0/16$' "$env_a"; then
        fail "case A: --trusted-proxies did not survive load_existing_config()+reconcile against a .env with no prior key: $(grep MAGPIE_TRUSTED_PROXIES "$env_a")"
    fi
    if ! grep -q '^MAGPIE_BIND_IP=10.3.3.107$' "$env_a"; then
        fail "case A: --bind-ip did not survive load_existing_config()+reconcile against a .env with no prior key: $(grep MAGPIE_BIND_IP "$env_a")"
    fi
    log "  ✓ case A (no prior key): both CLI overrides survived"

    # Case B: the actual regression -- a .env that ALREADY has both keys,
    # empty (exactly what generate_env_file() writes on every fresh
    # install). This is the case that was silently broken: FROM_CLI=true
    # but load_existing_config() would previously overwrite the CLI value
    # right back to the empty persisted one before reconcile ever saw it.
    local install_b="${TEST_DIR}/cli_override_b"
    mkdir -p "${install_b}/etc"
    local env_b="${install_b}/etc/.env"
    cat > "$env_b" << 'EOF'
MAGPIE_DATA_DIR=/opt/magpie/data
MAGPIE_TRUSTED_PROXIES=
MAGPIE_BIND_IP=
EOF
    run_update_env_surgery "$env_b" "172.20.0.0/16" "true" "10.3.3.107" "true"
    if ! grep -q '^MAGPIE_TRUSTED_PROXIES=172.20.0.0/16$' "$env_b"; then
        fail "case B (A1 regression): --trusted-proxies was clobbered back to the persisted empty value: $(grep MAGPIE_TRUSTED_PROXIES "$env_b")"
    fi
    if ! grep -q '^MAGPIE_BIND_IP=10.3.3.107$' "$env_b"; then
        fail "case B (A1 regression): --bind-ip was clobbered back to the persisted empty value: $(grep MAGPIE_BIND_IP "$env_b")"
    fi
    log "  ✓ case B (already-present, empty key -- the A1/#583 regression case): both CLI overrides survived"

    # Case C: a .env with a real (non-empty) prior value for both keys --
    # the CLI override must still win.
    local install_c="${TEST_DIR}/cli_override_c"
    mkdir -p "${install_c}/etc"
    local env_c="${install_c}/etc/.env"
    cat > "$env_c" << 'EOF'
MAGPIE_DATA_DIR=/opt/magpie/data
MAGPIE_TRUSTED_PROXIES=10.0.0.0/8
MAGPIE_BIND_IP=192.0.2.1
EOF
    run_update_env_surgery "$env_c" "172.20.0.0/16" "true" "10.3.3.107" "true"
    if ! grep -q '^MAGPIE_TRUSTED_PROXIES=172.20.0.0/16$' "$env_c"; then
        fail "case C: --trusted-proxies did not override a real prior value: $(grep MAGPIE_TRUSTED_PROXIES "$env_c")"
    fi
    if ! grep -q '^MAGPIE_BIND_IP=10.3.3.107$' "$env_c"; then
        fail "case C: --bind-ip did not override a real prior value: $(grep MAGPIE_BIND_IP "$env_c")"
    fi
    log "  ✓ case C (already-present, non-empty key): both CLI overrides won"

    # Case D: regression guard -- WITHOUT a CLI override this run
    # (*_FROM_CLI=false), the persisted values must still load and survive
    # untouched (the normal update-with-no-flags path).
    local install_d="${TEST_DIR}/cli_override_d"
    mkdir -p "${install_d}/etc"
    local env_d="${install_d}/etc/.env"
    cat > "$env_d" << 'EOF'
MAGPIE_DATA_DIR=/opt/magpie/data
MAGPIE_TRUSTED_PROXIES=10.0.0.0/8
MAGPIE_BIND_IP=192.0.2.1
EOF
    run_update_env_surgery "$env_d" "" "false" "" "false"
    if ! grep -q '^MAGPIE_TRUSTED_PROXIES=10.0.0.0/8$' "$env_d"; then
        fail "case D: MAGPIE_TRUSTED_PROXIES changed even though no --trusted-proxies was passed: $(grep MAGPIE_TRUSTED_PROXIES "$env_d")"
    fi
    if ! grep -q '^MAGPIE_BIND_IP=192.0.2.1$' "$env_d"; then
        fail "case D: MAGPIE_BIND_IP changed even though no --bind-ip was passed: $(grep MAGPIE_BIND_IP "$env_d")"
    fi
    log "  ✓ case D (no CLI override): persisted values load normally and are left untouched"
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

    # env_key_has_value(): true only for a present key with a real
    # (non-empty, non-whitespace-only) value -- false for absent, present-
    # but-empty, and present-but-whitespace-only.
    local env_file3="${TEST_DIR}/has_value.env"
    # printf, not a quoted heredoc, so the trailing spaces on the
    # whitespace-only line are preserved literally (a heredoc line ending
    # in bare trailing whitespace is easy to lose/miss on re-edit).
    printf 'MAGPIE_REAL=file\nMAGPIE_EMPTY=\nMAGPIE_WHITESPACE=   \n' > "$env_file3"
    if ! env_key_has_value "$env_file3" "MAGPIE_REAL"; then
        fail "env_key_has_value said a key with a real value has none"
    fi
    if env_key_has_value "$env_file3" "MAGPIE_EMPTY"; then
        fail "env_key_has_value treated a present-but-empty key as having a value"
    fi
    if env_key_has_value "$env_file3" "MAGPIE_WHITESPACE"; then
        fail "env_key_has_value treated a present-but-whitespace-only key as having a value"
    fi
    if env_key_has_value "$env_file3" "MAGPIE_ABSENT"; then
        fail "env_key_has_value treated an absent key as having a value"
    fi
    log "  ✓ env_key_has_value distinguishes a real value from absent/empty/whitespace-only"
}

# Test 9 (Copilot finding on PR #597): log_error() must render a literal
# `\n` in the SCRIPT'S OWN hardcoded die()/log_error() message text as a
# real line break (several call sites rely on this for multi-line
# messages), but must NOT interpret any OTHER escape sequence -- including
# in interpolated content the script didn't write itself (an operator CLI
# value, or output captured from a subprocess). `echo -e`/`printf '%b'`
# interpret the full escape set (\t, \a, \e, octal, ...) across the WHOLE
# message; \e in particular enables ANSI terminal-escape injection into
# this script's own error output.
test_log_error_only_interprets_literal_backslash_n() {
    log "Test 9: log_error() renders \\n but does not interpret other escapes from interpolated content"

    local out
    out=$(log_error "line one\nline two" 2>&1)
    if [[ "$out" != $'[magpie] ERROR: line one\nline two' ]]; then
        fail "log_error did not render a literal \\n as a real line break: $(printf '%q' "$out")"
    fi
    log "  ✓ a literal \\n in the message renders as a real line break"

    # \e (ESC, 0x1B) is the terminal-escape-injection payload; \a (BEL) is
    # a milder but still-unwanted example. Neither must be decoded.
    local hostile
    hostile=$(printf 'path\\e[31mFAKE\\e[0m\\abell')
    out=$(log_error "Invalid: $hostile" 2>&1)
    if [[ "$out" == *$'\x1b'* ]]; then
        fail "log_error decoded \\e into a real ESC byte from interpolated content -- ANSI injection: $(printf '%q' "$out")"
    fi
    if [[ "$out" == *$'\a'* ]]; then
        fail "log_error decoded \\a into a real BEL byte from interpolated content: $(printf '%q' "$out")"
    fi
    if [[ "$out" != *'\e[31mFAKE\e[0m\abell'* ]]; then
        fail "log_error did not preserve the hostile value's escape sequences as inert literal text: $(printf '%q' "$out")"
    fi
    log "  ✓ \\e/\\a in interpolated content stay inert literal text (no escape decoding)"
}

# Test 10 (Copilot follow-up on the log_error() fix, PR #597): the
# substitution log_error() uses to render a literal \n as a real line
# break operates on the WHOLE message, including interpolated content --
# so an unvalidated --install-dir containing a literal \n could still
# inject a fabricated extra output line (log injection, not the
# ANSI-escape-injection Test 9 covers) into cmd_status/cmd_logs/
# cmd_uninstall's error output, since none of them validated INSTALL_DIR's
# charset before it could reach verify_installation()'s die() calls (only
# cmd_update did). validate_install_dir_or_die() -- extracted from
# cmd_update()'s existing check, now shared by cmd_update/cmd_status/
# cmd_logs/cmd_uninstall -- closes this: INSTALL_DIR's charset (no
# backslash, no metacharacters) is validated before any of these commands
# ever calls verify_installation() or die() with it interpolated.
test_hostile_install_dir_rejected_before_reaching_die() {
    log "Test 10: a hostile --install-dir (embedded backslash-n) is rejected before it can reach die()/log_error() in status/logs/uninstall"

    local hostile='/opt/magpie\nFAKE INJECTED LINE'
    local cmd out
    for cmd in cmd_status cmd_logs cmd_uninstall; do
        if out=$(
            (
                INSTALL_DIR="$hostile" NONINTERACTIVE="true" YES="true" "$cmd"
            ) 2>&1
        ); then
            fail "$cmd accepted a hostile --install-dir containing a literal backslash-n"
        fi
        if ! echo "$out" | grep -qE "Invalid --install-dir|invalid characters"; then
            fail "$cmd did not report an install-dir validation failure for the hostile value: $out"
        fi
        # The hostile value's raw backslash-n legitimately appears verbatim
        # in this validation error's own text (it echoes the rejected
        # value) -- the injection question is whether it survived as a
        # literal two-char sequence (safe) or got rendered as a real line
        # break (log injection). Check for the exact substring spanning
        # both halves on one line, not just the second half's presence.
        if ! echo "$out" | grep -qF 'magpie\nFAKE INJECTED LINE'; then
            fail "$cmd's error output rendered the hostile value's embedded backslash-n as a real line break (log injection): $out"
        fi
        log "  ✓ $cmd rejects the hostile --install-dir before any die()/log_error() call could see it"
    done
}

# Run all tests
log "Running tests for the v0.2.0 installer rewrite (PR-3)"
log ""

test_generate_env_file_v020_keys
test_tier1_deprecated_flags_accepted_and_warn
test_reconcile_env_file_strips_and_adds
test_reconcile_env_file_migrates_legacy_bind_ip
test_583_trusted_proxies_upsert_in_place
test_cli_override_survives_load_existing_config
test_tier2_gate_blocks_and_bypasses
test_579_gate_install_warns_update_dies
test_upsert_and_strip_env_key_primitives
test_log_error_only_interprets_literal_backslash_n
test_hostile_install_dir_rejected_before_reaching_die

log ""
log "All tests passed!"
