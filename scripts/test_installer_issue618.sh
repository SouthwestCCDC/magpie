#!/bin/bash
# Test issue #618: fresh installs should explain first-boot admin-token delivery.
#
# Source the installer functions and stub compose so the init-result paths
# can be exercised without Docker, systemd, or root.

# shellcheck disable=SC2329  # test doubles are called by sourced installer functions

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_SCRIPT="${SCRIPT_DIR}/magpie-deploy.sh"
TEST_DIR="$(mktemp -d)"

cleanup() {
    rm -rf "$TEST_DIR"
}
trap cleanup EXIT

# shellcheck source=/dev/null
source <(sed '/^main "\$@"$/d' "$DEPLOY_SCRIPT")

set +e
set -uo pipefail

log() {
    echo "[test] $*"
}

fail() {
    echo "[test] FAIL: $*" >&2
    exit 1
}

assert_contains() {
    local output="$1" expected="$2" description="$3"
    if [[ "$output" == *"$expected"* ]]; then
        log "  ✓ ${description}"
    else
        fail "${description}: missing '${expected}'"
    fi
}

assert_not_contains() {
    local output="$1" unexpected="$2" description="$3"
    if [[ "$output" != *"$unexpected"* ]]; then
        log "  ✓ ${description}"
    else
        fail "${description}: unexpectedly found '${unexpected}'"
    fi
}

create_fake_install() {
    local name="$1" sink="$2"
    local install_dir="${TEST_DIR}/${name}"
    mkdir -p "${install_dir}/etc" "${install_dir}/data"
    if [[ "$sink" == "unset" ]]; then
        : > "${install_dir}/etc/.env"
    else
        printf 'MAGPIE_ADMIN_TOKEN_SINK=%s\n' "$sink" > "${install_dir}/etc/.env"
    fi
    printf '%s\n' "$install_dir"
}

run_init_with_stub() {
    local install_dir="$1" db_preexisted="$2" mock_result="$3"
    (
        # shellcheck disable=SC2034  # read by run_init() from the sourced installer
        INSTALL_DIR="$install_dir"
        DATA_DIR="${install_dir}/data"
        # shellcheck disable=SC2030  # report_init_token_status() reads this in the same test subshell
        DATA_DIR_DB_PREEXISTED="$db_preexisted"
        MOCK_INIT_RESULT="$mock_result"

        magpie_compose() {
            if [[ "$MOCK_INIT_RESULT" == "already-initialized" ]]; then
                printf '%s\n' "Admin token already exists" >&2
                return 1
            fi
            printf '%s\n' "Initialization completed without a token"
        }

        run_init
    )
}

test_database_state_is_recorded_from_the_database_file() {
    local data_dir="${TEST_DIR}/database-state"
    mkdir -p "$data_dir"
    # shellcheck disable=SC2034  # consumed by record_database_preexisting_state() from the sourced installer
    DATA_DIR="$data_dir"
    record_database_preexisting_state
    # shellcheck disable=SC2031  # read back the global value set by the sourced installer function
    [[ "$DATA_DIR_DB_PREEXISTED" == "false" ]] || fail "missing DB was marked pre-existing"

    : > "${data_dir}/magpie.db"
    record_database_preexisting_state
    # shellcheck disable=SC2031  # read back the global value set by the sourced installer function
    [[ "$DATA_DIR_DB_PREEXISTED" == "true" ]] || fail "existing magpie.db was not recorded"
    log "  ✓ prior state follows ${data_dir}/magpie.db"
}

test_fresh_init_error_reports_configured_sink() {
    local install_dir output
    install_dir="$(create_fake_install "fresh-exec" "exec")"
    output="$(run_init_with_stub "$install_dir" "false" "already-initialized" 2>&1)"

    assert_contains "$output" "Admin token was generated on first boot and delivered via the 'exec' sink." \
        "fresh init error reports configured sink"
    assert_not_contains "$output" "already initialized" \
        "fresh init error avoids pre-existing-database wording"
    assert_not_contains "$output" "sudo cat" "non-file sink omits file instructions"
    assert_contains "$output" "To mint an additional admin-scope token instead:" \
        "fresh init error keeps additional-token hint"
}

test_fresh_no_token_defaults_to_file_location() {
    local install_dir output
    install_dir="$(create_fake_install "fresh-file" "unset")"
    output="$(run_init_with_stub "$install_dir" "false" "no-token" 2>&1)"

    assert_contains "$output" "Admin token was generated on first boot and delivered via the 'file' sink." \
        "fresh no-token result defaults to file sink"
    assert_contains "$output" "sudo cat ${install_dir}/data/admin-token" \
        "fresh no-token result prints token-file command"
    assert_not_contains "$output" "already initialized" \
        "fresh no-token result avoids pre-existing-database wording"
    assert_contains "$output" "To mint an additional admin-scope token instead:" \
        "fresh no-token result keeps additional-token hint"
}

test_preexisting_database_keeps_initialized_wording() {
    local install_dir output
    install_dir="$(create_fake_install "preexisting" "file")"
    output="$(run_init_with_stub "$install_dir" "true" "already-initialized" 2>&1)"

    assert_contains "$output" "Database already initialized" \
        "pre-existing database keeps initialized wording"
    assert_not_contains "$output" "Admin token was generated on first boot" \
        "pre-existing database omits fresh-install wording"
    assert_contains "$output" "To mint an additional admin-scope token instead:" \
        "pre-existing database keeps additional-token hint"
}

main() {
    log "Testing issue #618 installer token messages..."
    test_database_state_is_recorded_from_the_database_file
    test_fresh_init_error_reports_configured_sink
    test_fresh_no_token_defaults_to_file_location
    test_preexisting_database_keeps_initialized_wording
    log "All issue #618 installer tests passed"
}

main "$@"
