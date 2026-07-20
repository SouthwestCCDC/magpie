#!/bin/bash
# Test script for issue #561 -- the update safety envelope: detection,
# capture, backup, the post-update assertion gate, and rollback.
#
# This exercises the *real* functions from magpie-deploy.sh (sourced, not
# reimplemented), same pattern as scripts/test_installer_v020.sh. Docker
# and systemd calls inside the functions under test are made harmless by
# shadowing `systemctl`/`docker`/`sleep` as bash functions in the
# subshells that need them (bash resolves a bare command name to a
# same-shell function before PATH, so this is a real override, not a
# mock library) -- no root, no running magpie install, and no long sleeps
# are required to run this file. The one genuine fault-injection test
# (test_rollback_fires_and_restores_prior_state) proves the actual
# recovery path: it corrupts the "post-swap" state on disk, runs
# assert_post_update() for real against that corrupted state (no
# container needed -- a real `docker compose exec` against a
# nonexistent project fails fast and IS the injected fault), and checks
# that rollback_to_prior() puts the original bytes back.
#
# Outer (this script's own) working directories are always lowercase
# (install_dir/data_dir) and only ever mapped to the sourced functions'
# uppercase globals (INSTALL_DIR/DATA_DIR) as an env-var prefix on the
# subshell that calls them -- same convention as test_installer_v020.sh
# -- so a value read outside a subshell is never confused with one set
# inside it.
#
# What this file does NOT cover (see the PR description for why): the
# real 0.1.x-two-container -> 0.2.0-bundled end-to-end upgrade (issue
# #561-B, needs a published prior-version image) and the full
# config x version-chain matrix (companion issue).

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

# Sets up a minimal fake install under a fresh directory: install_dir
# (with etc/.env + docker-compose.yml) and data_dir (with magpie.db).
# Echoes "install_dir data_dir" (space-separated, both single path
# components with no spaces of their own -- guaranteed by TEST_DIR/name)
# so callers can capture both with `read`.
setup_fake_install() {
    local name="$1"
    local db_content="${2:-OLD_DB_CONTENT}"
    local env_extra="${3:-}"

    local install_dir="${TEST_DIR}/${name}"
    local data_dir="${install_dir}/data"
    mkdir -p "${install_dir}/etc" "${data_dir}/artifacts"

    cat > "${install_dir}/etc/.env" << EOF
MAGPIE_DATA_DIR=${data_dir}
MAGPIE_HTTP_PORT=8080
MAGPIE_ADMIN_TOKEN_SINK=file
MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH=/data/admin-token
${env_extra}
EOF

    cat > "${install_dir}/docker-compose.yml" << 'EOF'
services:
  magpie:
    image: ghcr.io/southwestccdc/magpie:0.1.6-bundled
EOF

    printf '%s' "$db_content" > "${data_dir}/magpie.db"
    echo "${install_dir} ${data_dir}"
}

# ---------------------------------------------------------------------------
# health_check_url() -- sharp edge S6 (bind-IP awareness)
# ---------------------------------------------------------------------------
test_health_check_url_bind_ip_aware() {
    log "Test 1: health_check_url() probes the configured BIND_IP, not hardcoded loopback"

    local url
    url=$(BIND_IP="" HTTP_PORT=8080 health_check_url)
    [[ "$url" == "http://127.0.0.1:8080/health" ]] || fail "empty BIND_IP: expected loopback, got: $url"
    log "  ✓ empty BIND_IP -> loopback"

    url=$(BIND_IP="0.0.0.0" HTTP_PORT=8080 health_check_url)
    [[ "$url" == "http://127.0.0.1:8080/health" ]] || fail "0.0.0.0 BIND_IP: expected loopback fallback, got: $url"
    log "  ✓ BIND_IP=0.0.0.0 (all interfaces) -> loopback fallback"

    url=$(BIND_IP="10.3.3.107" HTTP_PORT=8080 health_check_url)
    [[ "$url" == "http://10.3.3.107:8080/health" ]] || fail "a real BIND_IP: expected it to be probed directly, got: $url"
    log "  ✓ a real (non-loopback) BIND_IP is probed directly -- this is the S6 fix"

    url=$(BIND_IP="::1" HTTP_PORT=8080 health_check_url)
    [[ "$url" == "http://[::1]:8080/health" ]] || fail "an IPv6 BIND_IP: expected bracketed host, got: $url"
    log "  ✓ an IPv6 BIND_IP is bracketed in the URL"
}

# ---------------------------------------------------------------------------
# wait_for_healthy() -- must return 1 (not warn-and-0) on a real timeout
# ---------------------------------------------------------------------------
test_wait_for_healthy_hard_fails_on_timeout() {
    log "Test 2: wait_for_healthy() returns 1 (not a swallowed warning) when nothing ever answers"

    # sleep is shadowed to a no-op so the 30-attempt retry loop completes
    # instantly instead of taking 60s -- this tests the return-code
    # contract, not the real retry cadence (which is exercised for real in
    # the live-verification section of the PR description).
    (
        sleep() { :; }
        BIND_IP="" HTTP_PORT=1 wait_for_healthy
    ) >/dev/null 2>&1
    local rc=$?
    [[ $rc -ne 0 ]] || fail "wait_for_healthy() returned 0 against a port nothing listens on -- the pre-#561 toothless warn-and-return-0 behavior would look exactly like this"
    log "  ✓ wait_for_healthy() returns nonzero on a real timeout (the gap this issue closes)"
}

# ---------------------------------------------------------------------------
# detect_upgrade_shape()
# ---------------------------------------------------------------------------
test_detect_upgrade_shape_signals() {
    log "Test 3: detect_upgrade_shape() detects a pre-0.2.0 two-container install from each signal independently"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "shape_tls")
    (
        INSTALL_DIR="$install_dir" PERSISTED_TLS_MODE="auto" detect_upgrade_shape
        [[ "$IS_CROSS_020" == "true" ]] || exit 1
    ) >/dev/null 2>&1 || fail "a persisted TLS_MODE=auto was not detected as a cross-0.2.0 upgrade"
    log "  ✓ persisted TLS_MODE=auto/manual/off is detected"

    read -r install_dir data_dir < <(setup_fake_install "shape_caddyfile")
    touch "${install_dir}/Caddyfile"
    (
        INSTALL_DIR="$install_dir" PERSISTED_TLS_MODE="" detect_upgrade_shape
        [[ "$IS_CROSS_020" == "true" ]] || exit 1
    ) >/dev/null 2>&1 || fail "a Caddyfile in INSTALL_DIR was not detected as a cross-0.2.0 upgrade signal"
    log "  ✓ a leftover Caddyfile is detected"

    read -r install_dir data_dir < <(setup_fake_install "shape_caddy_service")
    cat > "${install_dir}/docker-compose.yml" << 'EOF'
services:
  magpie:
    image: test
  caddy:
    image: caddy:2
EOF
    (
        INSTALL_DIR="$install_dir" PERSISTED_TLS_MODE="" detect_upgrade_shape
        [[ "$IS_CROSS_020" == "true" ]] || exit 1
    ) >/dev/null 2>&1 || fail "a caddy: service in the current docker-compose.yml was not detected"
    log "  ✓ a caddy: service in the current docker-compose.yml is detected"

    read -r install_dir data_dir < <(setup_fake_install "shape_no_bundled_image")
    cat > "${install_dir}/etc/.env" << EOF
MAGPIE_DATA_DIR=${data_dir}
MAGPIE_IMAGE=ghcr.io/southwestccdc/magpie:0.1.6
EOF
    (
        INSTALL_DIR="$install_dir" PERSISTED_TLS_MODE="" detect_upgrade_shape
        [[ "$IS_CROSS_020" == "true" ]] || exit 1
    ) >/dev/null 2>&1 || fail "a non--bundled MAGPIE_IMAGE was not detected as a cross-0.2.0 signal"
    log "  ✓ MAGPIE_IMAGE absent/not a -bundled tag is detected"

    # A clean 0.2.x install (bundled image, no TLS_MODE, no caddy service,
    # no leftover files) must NOT be flagged.
    read -r install_dir data_dir < <(setup_fake_install "shape_clean_020" "" "MAGPIE_IMAGE=ghcr.io/southwestccdc/magpie:0.2.0-bundled")
    (
        INSTALL_DIR="$install_dir" PERSISTED_TLS_MODE="" detect_upgrade_shape
        [[ "$IS_CROSS_020" == "false" ]] || exit 1
    ) >/dev/null 2>&1 || fail "a clean same-topology 0.2.x install was incorrectly flagged as a cross-0.2.0 upgrade"
    log "  ✓ a clean 0.2.x install is NOT flagged (same-topology update)"
}

# ---------------------------------------------------------------------------
# compute_backup_dir()
# ---------------------------------------------------------------------------
test_compute_backup_dir_format() {
    log "Test 4: compute_backup_dir() produces INSTALL_DIR/backups/<version>-<UTC-timestamp>"

    local dir
    dir=$(
        (
            INSTALL_DIR="/opt/magpie" MAGPIE_VERSION="0.2.0"
            compute_backup_dir
            echo "$BACKUP_DIR"
        )
    )
    [[ "$dir" =~ ^/opt/magpie/backups/0\.2\.0-[0-9]{8}T[0-9]{6}Z$ ]] || fail "unexpected backup dir format: $dir"
    log "  ✓ backup dir format: $dir"
}

# ---------------------------------------------------------------------------
# preflight_backup_space()
# ---------------------------------------------------------------------------
test_preflight_backup_space() {
    log "Test 5: preflight_backup_space() dies on insufficient space, no-ops under --no-backup"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "space_low")
    local out
    if out=$(
        (
            # Shadow df to report 0KB available, regardless of the real
            # filesystem -- forces the low-space path deterministically.
            df() { echo "Filesystem 1K-blocks Used Available Use% Mounted"; echo "test 100 100 0 100% /"; }
            INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" NO_BACKUP="false" \
            BACKUP_ARTIFACTS="link" BACKUP_DIR="${install_dir}/backups/x" \
            preflight_backup_space
        ) 2>&1
    ); then
        fail "preflight_backup_space() did not die with 0KB available: $out"
    fi
    echo "$out" | grep -qi "not enough free space" || fail "die() message did not explain the space shortfall: $out"
    log "  ✓ dies when free space is (simulated) insufficient"

    if ! out=$(
        (
            df() { echo "Filesystem 1K-blocks Used Available Use% Mounted"; echo "test 100 100 0 100% /"; }
            INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" NO_BACKUP="true" \
            BACKUP_ARTIFACTS="link" BACKUP_DIR="${install_dir}/backups/x" \
            preflight_backup_space
        ) 2>&1
    ); then
        fail "preflight_backup_space() died even with --no-backup (NO_BACKUP=true): $out"
    fi
    log "  ✓ --no-backup skips the space check entirely (no-op, never dies)"
}

# ---------------------------------------------------------------------------
# backup_data()
# ---------------------------------------------------------------------------
test_backup_data_snapshots_db_env_and_writes_manifest() {
    log "Test 6: backup_data() snapshots magpie.db + .env and writes a MANIFEST"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "backup_basic" "MY_DB_BYTES")
    local backup_dir="${install_dir}/backups/test"
    (
        systemctl() { return 0; }
        INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="$backup_dir" \
        NO_BACKUP="false" BACKUP_ARTIFACTS="skip" \
        PRIOR_MAGPIE_IMAGE="ghcr.io/southwestccdc/magpie:0.1.6-bundled" \
        PRIOR_GIT_REF="deadbeef" MAGPIE_VERSION="0.2.0" IS_CROSS_020="true" \
        backup_data
    ) >/dev/null 2>&1

    [[ "$(cat "${backup_dir}/data/magpie.db")" == "MY_DB_BYTES" ]] || fail "backup_data() did not snapshot magpie.db correctly"
    diff -q "${install_dir}/etc/.env" "${backup_dir}/data/.env" >/dev/null || fail "backup_data() did not snapshot .env correctly"
    [[ -f "${backup_dir}/MANIFEST" ]] || fail "backup_data() did not write a MANIFEST"
    grep -q "^source_image=ghcr.io/southwestccdc/magpie:0.1.6-bundled$" "${backup_dir}/MANIFEST" || fail "MANIFEST missing source_image"
    grep -q "^backup_artifacts_mode=skip$" "${backup_dir}/MANIFEST" || fail "MANIFEST did not record backup_artifacts_mode=skip"
    log "  ✓ magpie.db + .env snapshotted; MANIFEST written with expected fields"
}

test_backup_data_artifacts_modes() {
    log "Test 7: backup_data() --backup-artifacts link/copy/skip behave as documented"

    local install_dir data_dir backup_dir src_inode dst_inode

    # link: a hardlink snapshot shares the same inode as the source.
    read -r install_dir data_dir < <(setup_fake_install "backup_link")
    echo "artifact-bytes" > "${data_dir}/artifacts/blob1"
    backup_dir="${install_dir}/backups/link"
    (
        systemctl() { return 0; }
        INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="$backup_dir" \
        NO_BACKUP="false" BACKUP_ARTIFACTS="link" MAGPIE_VERSION="0.2.0" \
        backup_data
    ) >/dev/null 2>&1
    [[ -f "${backup_dir}/data/artifacts/blob1" ]] || fail "link mode: artifacts were not backed up at all"
    src_inode=$(stat -c %i "${data_dir}/artifacts/blob1")
    dst_inode=$(stat -c %i "${backup_dir}/data/artifacts/blob1")
    [[ "$src_inode" == "$dst_inode" ]] || fail "link mode: backup is not actually hardlinked to the source (different inodes: $src_inode vs $dst_inode)"
    log "  ✓ --backup-artifacts=link hardlinks (same inode)"

    # copy: same content, but a DIFFERENT inode (a real, independent copy).
    read -r install_dir data_dir < <(setup_fake_install "backup_copy")
    echo "artifact-bytes" > "${data_dir}/artifacts/blob1"
    backup_dir="${install_dir}/backups/copy"
    (
        systemctl() { return 0; }
        INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="$backup_dir" \
        NO_BACKUP="false" BACKUP_ARTIFACTS="copy" MAGPIE_VERSION="0.2.0" \
        backup_data
    ) >/dev/null 2>&1
    [[ -f "${backup_dir}/data/artifacts/blob1" ]] || fail "copy mode: artifacts were not backed up"
    src_inode=$(stat -c %i "${data_dir}/artifacts/blob1")
    dst_inode=$(stat -c %i "${backup_dir}/data/artifacts/blob1")
    [[ "$src_inode" != "$dst_inode" ]] || fail "copy mode: backup shares an inode with the source -- not actually an independent copy"
    diff -q "${data_dir}/artifacts/blob1" "${backup_dir}/data/artifacts/blob1" >/dev/null || fail "copy mode: content mismatch"
    log "  ✓ --backup-artifacts=copy makes an independent full copy"

    # skip: no artifacts directory in the backup at all.
    read -r install_dir data_dir < <(setup_fake_install "backup_skip")
    echo "artifact-bytes" > "${data_dir}/artifacts/blob1"
    backup_dir="${install_dir}/backups/skip"
    (
        systemctl() { return 0; }
        INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="$backup_dir" \
        NO_BACKUP="false" BACKUP_ARTIFACTS="skip" MAGPIE_VERSION="0.2.0" \
        backup_data
    ) >/dev/null 2>&1
    [[ ! -e "${backup_dir}/data/artifacts" ]] || fail "skip mode: artifacts were backed up despite --backup-artifacts=skip"
    log "  ✓ --backup-artifacts=skip omits the artifacts tree entirely"
}

test_backup_data_no_backup_skips_everything() {
    log "Test 8: backup_data() is a pure no-op under --no-backup"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "backup_noop")
    local backup_dir="${install_dir}/backups/noop"
    (
        systemctl() { return 0; }
        INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="$backup_dir" \
        NO_BACKUP="true" MAGPIE_VERSION="0.2.0" \
        backup_data
    ) >/dev/null 2>&1
    [[ ! -e "${backup_dir}/data" ]] || fail "--no-backup still created a data backup directory"
    log "  ✓ --no-backup creates no data backup at all"
}

# ---------------------------------------------------------------------------
# capture_prior_state() / capture_probe_state()
# ---------------------------------------------------------------------------
test_capture_prior_state_snapshots_config_and_skips_unreachable_probes() {
    log "Test 9: capture_prior_state() snapshots .env/docker-compose.yml and gracefully skips probes when nothing is running"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "capture_basic")
    local backup_dir="${install_dir}/backups/capture"
    (
        INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="$backup_dir" \
        HTTP_PORT=1 BIND_IP="" PRIOR_GIT_REF="" \
        capture_prior_state

        diff -q "${install_dir}/etc/.env" "${backup_dir}/rollback/.env" >/dev/null || exit 1
        diff -q "${install_dir}/docker-compose.yml" "${backup_dir}/rollback/docker-compose.yml" >/dev/null || exit 1
        [[ -z "$PROBE_TOKEN" ]] || exit 1
        [[ -z "$PROBE_ARTIFACT_PATH" ]] || exit 1
        [[ "$PRIOR_DATA_FORMAT_VERSION" == "0" ]] || exit 1
    ) >/dev/null 2>&1 || fail "capture_prior_state() did not snapshot config correctly or did not gracefully skip probes against an unreachable install"
    log "  ✓ .env/docker-compose.yml snapshotted; probes skipped cleanly (no container running) with PRIOR_DATA_FORMAT_VERSION defaulting to 0"
}

# ---------------------------------------------------------------------------
# assert_post_update() -- A1/A5/A6 always run; A2/A3/A4 skip when nothing
# was captured. compose_exec is shadowed to simulate the post-update
# container's responses deterministically (no real Docker needed).
# ---------------------------------------------------------------------------
test_assert_post_update_a1_short_circuits_on_unhealthy() {
    log "Test 10: assert_post_update() short-circuits on A1 failure without touching A5/A6"

    local called_marker
    called_marker="${TEST_DIR}/a5_a6_called"
    rm -f "$called_marker"

    (
        wait_for_healthy() { return 1; }
        compose_exec() { touch "$called_marker"; return 1; }
        HTTP_PORT=1 BIND_IP="" PRIOR_DATA_FORMAT_VERSION="0" PRIOR_TOKEN_ROW_COUNT="" \
        PROBE_TOKEN="" PROBE_ARTIFACT_PATH="" \
        assert_post_update
    ) >/dev/null 2>&1
    local rc=$?

    [[ $rc -ne 0 ]] || fail "assert_post_update() returned success despite a failed A1 health check"
    [[ ! -f "$called_marker" ]] || fail "assert_post_update() called compose_exec (A5/A6) even though A1 already failed -- should short-circuit"
    log "  ✓ A1 failure short-circuits the rest of the gate"
}

test_assert_post_update_skips_a2_a3_a4_when_nothing_captured() {
    log "Test 11: assert_post_update() passes when A1/A5/A6 are healthy and A2/A3/A4 are skipped (nothing captured)"

    (
        wait_for_healthy() { return 0; }
        compose_exec() {
            case "$*" in
                *"migrate --check"*) echo "Data-format version: 1 (current: 1)" ;;
                *"token list"*) echo "Total: 2 token(s)" ;;
                *) return 1 ;;
            esac
        }
        PRIOR_DATA_FORMAT_VERSION="1" PRIOR_TOKEN_ROW_COUNT="2" \
        PROBE_TOKEN="" PROBE_ARTIFACT_PATH="" PROBE_ARTIFACT_REF="" \
        assert_post_update
    ) >/dev/null 2>&1
    local rc=$?
    [[ $rc -eq 0 ]] || fail "assert_post_update() failed even though A1/A5/A6 all pass and A2/A3/A4 have nothing to check"
    log "  ✓ passes cleanly when A2/A3/A4 are vacuously satisfied"
}

test_assert_post_update_a5_fails_on_version_regression() {
    log "Test 12: assert_post_update() fails A5 if the data-format version goes backward"

    local out
    out=$(
        (
            wait_for_healthy() { return 0; }
            compose_exec() {
                case "$*" in
                    *"migrate --check"*) echo "Data-format version: 0 (current: 1)" ;;
                    *"token list"*) echo "Total: 2 token(s)" ;;
                    *) return 1 ;;
                esac
            }
            PRIOR_DATA_FORMAT_VERSION="1" PRIOR_TOKEN_ROW_COUNT="2" \
            PROBE_TOKEN="" PROBE_ARTIFACT_PATH="" \
            assert_post_update
            printf '%s\n' "${ASSERT_FAILURES[@]}"
        ) 2>&1
    )
    echo "$out" | grep -q "^A5 data-format version:" || fail "A5 did not fail on a version regression (1 -> 0): $out"
    log "  ✓ A5 fails when the version goes backward"
}

test_assert_post_update_a6_fails_on_row_count_change() {
    log "Test 13: assert_post_update() fails A6 if the token row count changes"

    local out
    out=$(
        (
            wait_for_healthy() { return 0; }
            compose_exec() {
                case "$*" in
                    *"migrate --check"*) echo "Data-format version: 1 (current: 1)" ;;
                    *"token list"*) echo "Total: 1 token(s)" ;;
                    *) return 1 ;;
                esac
            }
            PRIOR_DATA_FORMAT_VERSION="1" PRIOR_TOKEN_ROW_COUNT="2" \
            PROBE_TOKEN="" PROBE_ARTIFACT_PATH="" \
            assert_post_update
            printf '%s\n' "${ASSERT_FAILURES[@]}"
        ) 2>&1
    )
    echo "$out" | grep -q "^A6 token row count:" || fail "A6 did not fail on a row count change (2 -> 1): $out"
    log "  ✓ A6 fails when the token row count changes"
}

# ---------------------------------------------------------------------------
# rollback_to_prior() -- the fault-injection proof
# ---------------------------------------------------------------------------
test_rollback_fires_and_restores_prior_state() {
    log "Test 14: FAULT INJECTION -- a failed assert triggers rollback_to_prior(), which restores the prior .env/docker-compose.yml/magpie.db"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "rollback_proof" "OLD_DB_CONTENT")
    local old_env old_compose backup_dir
    old_env=$(cat "${install_dir}/etc/.env")
    old_compose=$(cat "${install_dir}/docker-compose.yml")
    backup_dir="${install_dir}/backups/proof"

    # capture_prior_state() + backup_data(): snapshot the "prior" (good)
    # state before any mutation, exactly as cmd_update() does.
    (
        systemctl() { return 0; }
        INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="$backup_dir" \
        HTTP_PORT=1 BIND_IP="" PRIOR_GIT_REF="" MAGPIE_VERSION="0.2.0" \
        NO_BACKUP="false" BACKUP_ARTIFACTS="skip"
        capture_prior_state
        preflight_backup_space
        backup_data
    ) >/dev/null 2>&1

    [[ -f "${backup_dir}/data/magpie.db" ]] || fail "setup problem: backup_data() did not produce a DB backup to roll back from"

    # Inject the fault: simulate a completed (but broken) swap by
    # overwriting .env, docker-compose.yml, and magpie.db with different
    # ("new", corrupted) content -- exactly what a failed update would
    # have left behind.
    echo "NEW_BROKEN_ENV=1" > "${install_dir}/etc/.env"
    echo "NEW_BROKEN_COMPOSE=1" > "${install_dir}/docker-compose.yml"
    echo "NEW_CORRUPTED_DB" > "${data_dir}/magpie.db"

    # rollback_to_prior() always die()s (reporting either a clean rollback
    # or a failed one) -- capture its output/exit rather than expecting a
    # normal return.
    local out
    out=$(
        (
            systemctl() { return 0; }
            wait_for_healthy() { return 0; }  # simulate the restored topology coming up healthy
            docker() { return 1; }  # no prior image to inspect/pull in this fixture (PRIOR_MAGPIE_IMAGE is empty)
            INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="$backup_dir" \
            NO_BACKUP="false" PRIOR_MAGPIE_IMAGE="" PRIOR_GIT_REF="" \
            rollback_to_prior
        ) 2>&1
    )

    [[ "$(cat "${install_dir}/etc/.env")" == "$old_env" ]] || fail "rollback_to_prior() did not restore the prior .env. Got: $(cat "${install_dir}/etc/.env")"
    [[ "$(cat "${install_dir}/docker-compose.yml")" == "$old_compose" ]] || fail "rollback_to_prior() did not restore the prior docker-compose.yml"
    [[ "$(cat "${data_dir}/magpie.db")" == "OLD_DB_CONTENT" ]] || fail "rollback_to_prior() did not restore the prior magpie.db -- got: $(cat "${data_dir}/magpie.db")"
    echo "$out" | grep -qi "ROLLED BACK to the prior version" || fail "rollback_to_prior() did not report a clean rollback: $out"
    log "  ✓ FAULT INJECTED (corrupted .env/docker-compose.yml/magpie.db) -> rollback_to_prior() restored all three to their exact prior bytes"
}

test_rollback_reports_loudly_when_restore_itself_unhealthy() {
    log "Test 15: rollback_to_prior() reports a LOUD failure (not a silent one) if the restored topology doesn't come up healthy"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "rollback_double_fail" "OLD_DB_CONTENT")
    local backup_dir="${install_dir}/backups/doublefail"
    (
        systemctl() { return 0; }
        # Standalone assignments (not command-prefix form) -- all consumed
        # by capture_prior_state()/backup_data() below, sourced from
        # magpie-deploy.sh.
        # shellcheck disable=SC2034
        INSTALL_DIR="$install_dir"
        # shellcheck disable=SC2034
        DATA_DIR="$data_dir"
        BACKUP_DIR="$backup_dir"
        # shellcheck disable=SC2034
        HTTP_PORT=1
        # shellcheck disable=SC2034
        BIND_IP=""
        # shellcheck disable=SC2034
        PRIOR_GIT_REF=""
        # shellcheck disable=SC2034
        MAGPIE_VERSION="0.2.0"
        # shellcheck disable=SC2034
        NO_BACKUP="false"
        # shellcheck disable=SC2034
        BACKUP_ARTIFACTS="skip"
        capture_prior_state
        backup_data
    ) >/dev/null 2>&1

    local out
    out=$(
        (
            systemctl() { return 0; }
            wait_for_healthy() { return 1; }  # even the restored topology fails to come up
            docker() { return 1; }
            INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="$backup_dir" \
            NO_BACKUP="false" PRIOR_MAGPIE_IMAGE="" PRIOR_GIT_REF="" \
            rollback_to_prior
        ) 2>&1
    )

    echo "$out" | grep -qi "did not come up healthy" || fail "rollback_to_prior() did not clearly report that the restore itself failed: $out"
    echo "$out" | grep -qi "Manual recovery needed" || fail "rollback_to_prior() did not point to manual recovery on a failed restore: $out"
    echo "$out" | grep -qF "$backup_dir" || fail "rollback_to_prior()'s failure message did not include the backup location for manual recovery: $out"
    log "  ✓ a failed restore is a loud, actionable die() with the backup location -- never a silent retry"
}

# ---------------------------------------------------------------------------
# prune_old_backups()
# ---------------------------------------------------------------------------
test_prune_old_backups_keeps_newest_n() {
    log "Test 16: prune_old_backups() keeps only the KEEP_BACKUPS most recent backups"

    local install_dir="${TEST_DIR}/prune_test"
    mkdir -p "${install_dir}/backups"
    local i
    for i in 1 2 3 4 5; do
        mkdir -p "${install_dir}/backups/0.2.${i}-2026010${i}T000000Z"
    done

    (
        INSTALL_DIR="$install_dir" KEEP_BACKUPS="2" prune_old_backups
    ) >/dev/null 2>&1

    local remaining
    remaining=$(find "${install_dir}/backups" -mindepth 1 -maxdepth 1 -type d | wc -l)
    [[ "$remaining" -eq 2 ]] || fail "prune_old_backups() left ${remaining} backups, expected 2 (KEEP_BACKUPS=2)"

    [[ -d "${install_dir}/backups/0.2.4-20260104T000000Z" ]] || fail "prune_old_backups() removed a backup it should have kept (0.2.4)"
    [[ -d "${install_dir}/backups/0.2.5-20260105T000000Z" ]] || fail "prune_old_backups() removed a backup it should have kept (0.2.5)"
    [[ ! -d "${install_dir}/backups/0.2.1-20260101T000000Z" ]] || fail "prune_old_backups() kept an old backup it should have pruned (0.2.1)"
    log "  ✓ kept the 2 newest, pruned the rest"
}

test_prune_old_backups_noop_under_the_limit() {
    log "Test 17: prune_old_backups() is a no-op when there are fewer backups than KEEP_BACKUPS"

    local install_dir="${TEST_DIR}/prune_noop"
    mkdir -p "${install_dir}/backups/0.2.0-20260101T000000Z"

    (
        INSTALL_DIR="$install_dir" KEEP_BACKUPS="3" prune_old_backups
    ) >/dev/null 2>&1

    [[ -d "${install_dir}/backups/0.2.0-20260101T000000Z" ]] || fail "prune_old_backups() removed the only backup even though it's under the KEEP_BACKUPS limit"
    log "  ✓ no-op when under the retention limit"
}

# ---------------------------------------------------------------------------
# run_data_migration()
# ---------------------------------------------------------------------------
test_run_data_migration_failure_propagates() {
    log "Test 18: run_data_migration() returns nonzero and logs the failure when magpie-ctl migrate fails"

    local out
    out=$(
        (
            compose_exec() { echo "magpie-ctl: command not found" >&2; return 127; }
            run_data_migration
        ) 2>&1
    )
    local rc=$?
    [[ $rc -ne 0 ]] || fail "run_data_migration() returned success despite compose_exec failing"
    echo "$out" | grep -qi "magpie-ctl migrate failed" || fail "run_data_migration() did not log a clear failure message: $out"
    log "  ✓ a failed migrate propagates as a nonzero return with a clear message"
}

# ---------------------------------------------------------------------------
# cmd_update() flag validation for the new envelope options
# ---------------------------------------------------------------------------
test_cmd_update_validates_envelope_flags() {
    log "Test 19: cmd_update() validates --keep-backups/--backup-artifacts before acting on them"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "flag_validation")
    local out
    if out=$(
        (
            INSTALL_DIR="$install_dir" NONINTERACTIVE="true" ACCEPT_EMPTY_TRUSTED_PROXIES="true" \
            KEEP_BACKUPS="not-a-number" BACKUP_ARTIFACTS="link" \
            cmd_update
        ) 2>&1
    ); then
        fail "cmd_update() proceeded with a non-numeric --keep-backups"
    fi
    echo "$out" | grep -q "Invalid --keep-backups" || fail "cmd_update() did not report the invalid --keep-backups value: $out"
    log "  ✓ non-numeric --keep-backups is rejected"

    if out=$(
        (
            INSTALL_DIR="$install_dir" NONINTERACTIVE="true" ACCEPT_EMPTY_TRUSTED_PROXIES="true" \
            KEEP_BACKUPS="3" BACKUP_ARTIFACTS="bogus" \
            cmd_update
        ) 2>&1
    ); then
        fail "cmd_update() proceeded with an invalid --backup-artifacts value"
    fi
    echo "$out" | grep -q "Invalid --backup-artifacts" || fail "cmd_update() did not report the invalid --backup-artifacts value: $out"
    log "  ✓ an unrecognized --backup-artifacts value is rejected"
}

# Run all tests
log "Running tests for the update safety envelope (issue #561)"
log ""

test_health_check_url_bind_ip_aware
test_wait_for_healthy_hard_fails_on_timeout
test_detect_upgrade_shape_signals
test_compute_backup_dir_format
test_preflight_backup_space
test_backup_data_snapshots_db_env_and_writes_manifest
test_backup_data_artifacts_modes
test_backup_data_no_backup_skips_everything
test_capture_prior_state_snapshots_config_and_skips_unreachable_probes
test_assert_post_update_a1_short_circuits_on_unhealthy
test_assert_post_update_skips_a2_a3_a4_when_nothing_captured
test_assert_post_update_a5_fails_on_version_regression
test_assert_post_update_a6_fails_on_row_count_change
test_rollback_fires_and_restores_prior_state
test_rollback_reports_loudly_when_restore_itself_unhealthy
test_prune_old_backups_keeps_newest_n
test_prune_old_backups_noop_under_the_limit
test_run_data_migration_failure_propagates
test_cmd_update_validates_envelope_flags

log ""
log "All tests passed!"
