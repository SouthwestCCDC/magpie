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

    # The IPv6 unspecified address ("::", and any equivalent
    # all-zero/all-compressed form) means "listen on every interface" --
    # same as 0.0.0.0 for IPv4 -- and is not itself connectable. Probing
    # it directly would time out and falsely trigger a rollback on an
    # install that's actually fine.
    url=$(BIND_IP="::" HTTP_PORT=8080 health_check_url)
    [[ "$url" == "http://127.0.0.1:8080/health" ]] || fail "BIND_IP='::' (IPv6 all-interfaces): expected loopback fallback, got: $url"
    log "  ✓ BIND_IP='::' (IPv6 all interfaces) -> loopback fallback"

    url=$(BIND_IP="0:0:0:0:0:0:0:0" HTTP_PORT=8080 health_check_url)
    [[ "$url" == "http://127.0.0.1:8080/health" ]] || fail "BIND_IP='0:0:0:0:0:0:0:0' (fully-expanded IPv6 all-interfaces): expected loopback fallback, got: $url"
    log "  ✓ a fully-expanded all-zero IPv6 address also falls back to loopback"

    # ::1 (loopback, contains a '1') must NOT be caught by the same
    # all-zero fallback -- a real, connectable address.
    url=$(BIND_IP="::1" HTTP_PORT=8080 health_check_url)
    [[ "$url" == "http://[::1]:8080/health" ]] || fail "BIND_IP='::1' must still be probed directly (not treated as all-interfaces), got: $url"
    log "  ✓ ::1 (a real address, not all-zero) is still probed directly"
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

# Regression test for a Copilot finding: detect_version() only requires
# pyproject.toml's `version = "..."` line to parse at all -- the quoted
# content itself (from a corrupted or malicious --from-source checkout)
# isn't otherwise validated before compute_backup_dir() embeds it in a
# filesystem path this script (running as root) creates and writes into.
test_compute_backup_dir_rejects_unsafe_version() {
    log "Test 4b: compute_backup_dir() rejects a MAGPIE_VERSION containing unsafe characters"

    local out
    if out=$(
        (
            INSTALL_DIR="/opt/magpie" MAGPIE_VERSION="../../etc"
            compute_backup_dir
        ) 2>&1
    ); then
        fail "compute_backup_dir() did not reject a MAGPIE_VERSION containing '/' and '..': $out"
    fi
    echo "$out" | grep -qi "contains characters outside" || fail "compute_backup_dir() rejected the unsafe version but without a clear message: $out"
    log "  ✓ a MAGPIE_VERSION containing path-traversal characters is rejected before use in a filesystem path"
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

# Regression test for a Copilot finding: a symlink at the resolved
# admin-token host path would otherwise be silently followed by `cp`,
# potentially backing up an arbitrary host file the (non-root,
# privilege-dropped) container planted a symlink to under /data.
test_backup_data_refuses_symlinked_admin_token() {
    log "Test 6c: backup_data() refuses to back up a symlinked admin-token rather than following it"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "backup_symlink_token")
    echo "root-only-secret" > "${TEST_DIR}/host-secret-file"
    ln -s "${TEST_DIR}/host-secret-file" "${data_dir}/admin-token"

    local backup_dir="${install_dir}/backups/symlinktoken"
    local out
    out=$(
        (
            systemctl() { return 0; }
            INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="$backup_dir" \
            NO_BACKUP="false" BACKUP_ARTIFACTS="skip" MAGPIE_VERSION="0.2.0" \
            backup_data
        ) 2>&1
    )

    [[ ! -e "${backup_dir}/data/admin-token" ]] || fail "backup_data() followed a symlinked admin-token and backed up its target's contents: $(cat "${backup_dir}/data/admin-token" 2>/dev/null)"
    echo "$out" | grep -qi "is a symlink" || fail "backup_data() did not explain why the symlinked admin-token was skipped: $out"
    log "  ✓ a symlinked admin-token is refused, not followed"
}

# Regression test for a Copilot finding: backup_data()/rollback_to_prior()
# derive a host path from the operator-controlled
# MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH via `${DATA_DIR}${token_file#/data}` --
# an unvalidated value like "/data/../../etc/shadow" would resolve OUTSIDE
# DATA_DIR on the host, and this script runs as root.
test_resolve_admin_token_host_path_rejects_traversal() {
    log "Test 6b: resolve_admin_token_host_path() rejects a path outside /data or containing '..'"

    local out
    out=$(DATA_DIR="/opt/magpie/data" resolve_admin_token_host_path "/data/../../etc/shadow" 2>&1)
    local rc=$?
    [[ $rc -ne 0 ]] || fail "resolve_admin_token_host_path() accepted a '..'-containing path: $out"
    [[ -z "$out" || "$out" != /* ]] || fail "resolve_admin_token_host_path() printed a resolved path despite rejecting the input: $out"
    log "  ✓ a '..'-containing sink path is rejected, not resolved"

    out=$(DATA_DIR="/opt/magpie/data" resolve_admin_token_host_path "/etc/shadow" 2>&1)
    rc=$?
    [[ $rc -ne 0 ]] || fail "resolve_admin_token_host_path() accepted a path outside /data: $out"
    log "  ✓ a path not under /data is rejected"

    out=$(DATA_DIR="/opt/magpie/data" resolve_admin_token_host_path "/data/subdir/admin-token" 2>/dev/null)
    rc=$?
    [[ $rc -eq 0 ]] || fail "resolve_admin_token_host_path() rejected a legitimate /data/... path"
    [[ "$out" == "/opt/magpie/data/subdir/admin-token" ]] || fail "resolve_admin_token_host_path() resolved a legitimate path incorrectly: $out"
    log "  ✓ a legitimate /data/... path resolves correctly under DATA_DIR"

    # Regression: a bare `*..*` glob would ALSO reject a legitimate
    # filename that merely contains ".." as a substring, not a path
    # traversal segment.
    out=$(DATA_DIR="/opt/magpie/data" resolve_admin_token_host_path "/data/admin-token..bak" 2>/dev/null)
    rc=$?
    [[ $rc -eq 0 ]] || fail "resolve_admin_token_host_path() rejected a legitimate filename that merely contains '..' as a substring (not a path-traversal segment): admin-token..bak"
    [[ "$out" == "/opt/magpie/data/admin-token..bak" ]] || fail "resolve_admin_token_host_path() resolved 'admin-token..bak' incorrectly: $out"
    log "  ✓ a filename merely containing '..' as a substring (not a traversal segment) is accepted"
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

# Regression test for a Copilot finding: capture_prior_state() runs
# BEFORE anything is mutated (the service is still running normally) and
# BEFORE backup_data()'s swap-window subshell exists to catch anything --
# its own mkdir/cp calls were unguarded under this script's
# `set -euo pipefail`. A failure here (disk full is the plausible cause)
# must die() directly and clearly, NOT call rollback_to_prior() (which
# restores FROM these very files, so it can't recover their own creation
# failure) and NOT abort raw via an unguarded `set -e` exit.
test_capture_prior_state_cp_failure_dies_clearly_under_real_errexit() {
    log "Test 9b: a real capture_prior_state() cp failure, with errexit PRESERVED, dies clearly rather than aborting bare or calling rollback_to_prior()"

    if [[ "$(id -u)" -eq 0 ]]; then
        log "  (skipped: running as root -- cannot simulate a permission-denied cp failure via chmod)"
        return 0
    fi

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "capture_cp_fail")
    local backup_dir="${install_dir}/backups/capturefail"
    # Force a REAL cp failure: the source .env is unreadable, so
    # capture_prior_state()'s very first cp fails for real (permission
    # denied).
    chmod 000 "${install_dir}/etc/.env"

    local out
    out=$(
        (
            set -euo pipefail  # magpie-deploy.sh's own real semantics
            INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="$backup_dir" \
            HTTP_PORT=1 BIND_IP="" PRIOR_GIT_REF="" \
            capture_prior_state
        ) 2>&1
    )
    local rc=$?
    chmod 644 "${install_dir}/etc/.env" 2>/dev/null || true  # restore perms so TEST_DIR cleanup can remove it

    [[ $rc -ne 0 ]] || fail "capture_prior_state() reported success despite a real cp failure snapshotting .env"
    echo "$out" | grep -qi "Failed to snapshot .*\.env" || fail "the specific capture-step failure message was lost: $out"
    echo "$out" | grep -qi "Rolling back to the prior install" && fail "capture_prior_state()'s own failure incorrectly called rollback_to_prior() -- it restores FROM the files that just failed to be created, which can't work: $out"
    log "  ✓ a real capture_prior_state() cp failure, with errexit preserved, dies clearly -- not a bare abort, and not a broken call to rollback_to_prior()"
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

# Regression test (A4 half of the explicit-ref fix, see the
# capture_probe_state() version above): assert_post_update()'s A4 check
# must query 'info path:ref' by the EXPLICIT captured tag, not the bare
# path (which resolves via magpie's implicit ":latest" default) -- it has
# to verify the actual tag PROBE_ARTIFACT_REF names, not whatever "latest"
# happens to be post-update.
test_assert_post_update_a4_queries_info_by_explicit_ref() {
    log "Test 11b: assert_post_update()'s A4 check queries 'info path:tag' explicitly, not the bare path"

    local calls_log="${TEST_DIR}/a4_explicit_ref_calls.log"
    rm -f "$calls_log"
    (
        wait_for_healthy() { return 0; }
        compose_exec() {
            echo "$*" >> "$calls_log"
            case "$*" in
                *"migrate --check"*) echo "Data-format version: 1 (current: 1)" ;;
                *"token list"*) echo "Total: 2 token(s)" ;;
                *"info myproj/myart:v1"*)
                    printf 'Hash:        deadbeef123\n'
                    ;;
                *"get "*) echo "Downloaded: /tmp/x" ;;
                *) return 1 ;;
            esac
        }
        PRIOR_DATA_FORMAT_VERSION="1" PRIOR_TOKEN_ROW_COUNT="2" \
        PROBE_TOKEN="mgp_probe" PROBE_ARTIFACT_PATH="myproj/myart" PROBE_ARTIFACT_REF="v1" \
        PROBE_ARTIFACT_SHA256="deadbeef123" \
        assert_post_update
    ) >/dev/null 2>&1

    grep -qF "info myproj/myart:v1" "$calls_log" \
        || fail "assert_post_update()'s A4 check did not query 'info path:tag' explicitly: $(cat "$calls_log")"
    log "  ✓ A4 queries the explicit ref, not the bare (implicit-:latest) path"
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

# Regression tests for a bug a live-container run caught: A5/A6/A2 each
# derive a "post_*" local (post_version/post_row_count/post_hash) INSIDE an
# `if X=$(compose_exec ...); then post_*=...; fi` block -- if compose_exec
# itself fails outright (not just returns unexpected content), the `then`
# branch never runs and post_* is left truly unset. Under this script's own
# `set -euo pipefail`, referencing an unset `local` (declared without an
# initial value) throws "unbound variable" instead of behaving like an
# empty string -- these tests simulate exactly that failure mode (as
# opposed to Tests 12/13 above, which simulate compose_exec SUCCEEDING with
# unexpected content) to prove assert_post_update() reports a clean
# failure rather than crashing.
test_assert_post_update_a5_survives_total_compose_exec_failure() {
    log "Test 13b: assert_post_update() does not crash (unbound variable) when compose_exec fails outright for A5"

    local out
    out=$(
        (
            wait_for_healthy() { return 0; }
            compose_exec() { return 1; }  # fails for every call, including A5's migrate --check
            PRIOR_DATA_FORMAT_VERSION="1" PRIOR_TOKEN_ROW_COUNT="" \
            PROBE_TOKEN="" PROBE_ARTIFACT_PATH="" \
            assert_post_update
            apu_rc=$?
            printf '%s\n' "${ASSERT_FAILURES[@]}"
            # Re-exits with assert_post_update()'s own code -- otherwise
            # `$?` after the command substitution below would reflect the
            # printf above (always 0), not the check actually under test.
            exit "$apu_rc"
        ) 2>&1
    )
    local rc=$?
    [[ $rc -ne 0 ]] || fail "assert_post_update() returned success despite compose_exec failing outright"
    echo "$out" | grep -q "unbound variable" && fail "assert_post_update() crashed with 'unbound variable' instead of reporting a clean A5 failure: $out"
    echo "$out" | grep -q "^A5 data-format version: could not read" || fail "assert_post_update() did not report A5 as unreadable when compose_exec failed outright: $out"
    log "  ✓ a total compose_exec failure for A5 is reported cleanly, not a crash"
}

test_assert_post_update_a6_survives_total_compose_exec_failure() {
    log "Test 13c: assert_post_update() does not crash (unbound variable) when compose_exec fails outright for A6"

    local out
    out=$(
        (
            wait_for_healthy() { return 0; }
            compose_exec() {
                case "$*" in
                    *"migrate --check"*) echo "Data-format version: 1 (current: 1)" ;;
                    *) return 1 ;;  # fails A6's token list (and anything else)
                esac
            }
            PRIOR_DATA_FORMAT_VERSION="1" PRIOR_TOKEN_ROW_COUNT="2" \
            PROBE_TOKEN="" PROBE_ARTIFACT_PATH="" \
            assert_post_update
            apu_rc=$?
            printf '%s\n' "${ASSERT_FAILURES[@]}"
            exit "$apu_rc"
        ) 2>&1
    )
    local rc=$?
    [[ $rc -ne 0 ]] || fail "assert_post_update() returned success despite compose_exec failing outright for A6"
    echo "$out" | grep -q "unbound variable" && fail "assert_post_update() crashed with 'unbound variable' instead of reporting a clean A6 failure: $out"
    echo "$out" | grep -q "^A6 token row count: could not list tokens" || fail "assert_post_update() did not report A6 as unreadable when compose_exec failed outright: $out"
    log "  ✓ a total compose_exec failure for A6 is reported cleanly, not a crash"
}

test_assert_post_update_a2_survives_total_compose_exec_failure() {
    log "Test 13d: assert_post_update() does not crash (unbound variable) when compose_exec fails outright for A2/A4"

    local out
    out=$(
        (
            wait_for_healthy() { return 0; }
            compose_exec() {
                case "$*" in
                    *"migrate --check"*) echo "Data-format version: 1 (current: 1)" ;;
                    *"token list"*) echo "Total: 2 token(s)" ;;
                    *"info"*) return 1 ;;  # fails A2/A4's magpie info call
                    *) return 1 ;;
                esac
            }
            PRIOR_DATA_FORMAT_VERSION="1" PRIOR_TOKEN_ROW_COUNT="2" \
            PROBE_TOKEN="mgp_probe" PROBE_ARTIFACT_PATH="test/probe" PROBE_ARTIFACT_REF="latest" \
            PROBE_ARTIFACT_SHA256="deadbeef" \
            assert_post_update
            apu_rc=$?
            printf '%s\n' "${ASSERT_FAILURES[@]}"
            exit "$apu_rc"
        ) 2>&1
    )
    local rc=$?
    [[ $rc -ne 0 ]] || fail "assert_post_update() returned success despite compose_exec failing outright for A2/A4"
    echo "$out" | grep -q "unbound variable" && fail "assert_post_update() crashed with 'unbound variable' instead of reporting a clean A4 failure: $out"
    echo "$out" | grep -q "^A4 tag resolution: could not read" || fail "assert_post_update() did not report A4 as unreadable when compose_exec failed outright: $out"
    log "  ✓ a total compose_exec failure for A2/A4 is reported cleanly, not a crash"
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

# Regression test for a Copilot finding: the ephemeral probe token
# capture_probe_state() mints (persisted into the OLD container's DB
# BEFORE backup_data() snapshots it) is therefore present in the restored
# magpie.db too on rollback -- assert_post_update()'s own revoke only ever
# reached the NEW (post-swap, since-discarded) container's copy. A
# successful rollback must clean up that leftover admin-scoped token
# against the just-restored (old) container.
test_rollback_revokes_probe_token_after_healthy_restore() {
    log "Test 15b: rollback_to_prior() revokes the probe token against the restored container once it's healthy again"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "rollback_probe_revoke" "OLD_DB_CONTENT")
    local backup_dir="${install_dir}/backups/proberevoke"
    (
        systemctl() { return 0; }
        INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="$backup_dir" \
        HTTP_PORT=1 BIND_IP="" PRIOR_GIT_REF="" MAGPIE_VERSION="0.2.0" \
        NO_BACKUP="false" BACKUP_ARTIFACTS="skip"
        capture_prior_state
        backup_data
    ) >/dev/null 2>&1

    local calls_log="${TEST_DIR}/probe_revoke_calls.log"
    rm -f "$calls_log"
    (
        systemctl() { return 0; }
        wait_for_healthy() { return 0; }  # restored topology comes up healthy
        docker() { return 1; }
        compose_exec() { echo "$*" >> "$calls_log"; return 0; }
        INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="$backup_dir" \
        NO_BACKUP="false" PRIOR_MAGPIE_IMAGE="" PRIOR_GIT_REF="" \
        PROBE_TOKEN_NAME="magpie-update-probe-99999" \
        rollback_to_prior
    ) >/dev/null 2>&1

    grep -qF "magpie magpie-ctl token revoke magpie-update-probe-99999" "$calls_log" \
        || fail "rollback_to_prior() did not revoke the probe token against the restored container after a healthy rollback: $(cat "$calls_log" 2>/dev/null || echo "<no calls logged>")"
    log "  ✓ the probe token is revoked against the restored container once rollback confirms it's healthy"
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

# Regression test for a Copilot finding: a LEXICOGRAPHIC sort of
# "<version>-<timestamp>" names breaks across a digit-count boundary in
# the version -- "0.10.0-..." sorts BEFORE "0.9.0-..." as plain text, even
# though 0.10.0 is the newer backup -- which could prune the wrong,
# actually-newer one. Directory names are deliberately chosen to trigger
# exactly that failure mode if sorting were still name-based; mtimes are
# set explicitly (touch -d) so the test doesn't depend on real-time
# ordering during a fast loop.
test_prune_old_backups_sorts_by_mtime_not_name() {
    log "Test 17b: prune_old_backups() sorts by directory mtime, not by name (digit-boundary regression)"

    local install_dir="${TEST_DIR}/prune_digit_boundary"
    mkdir -p "${install_dir}/backups/0.10.0-20260101T000000Z"
    mkdir -p "${install_dir}/backups/0.9.0-20260102T000000Z"
    # 0.10.0 is chronologically OLDER (touched first) despite sorting
    # first lexicographically; 0.9.0 is chronologically NEWER despite
    # sorting last lexicographically.
    touch -d "2026-01-01T00:00:00" "${install_dir}/backups/0.10.0-20260101T000000Z"
    touch -d "2026-01-02T00:00:00" "${install_dir}/backups/0.9.0-20260102T000000Z"

    (
        INSTALL_DIR="$install_dir" KEEP_BACKUPS="1" prune_old_backups
    ) >/dev/null 2>&1

    [[ -d "${install_dir}/backups/0.9.0-20260102T000000Z" ]] || fail "prune_old_backups() pruned the chronologically NEWER backup (0.9.0, mtime 2026-01-02) -- sorting by name instead of mtime"
    [[ ! -d "${install_dir}/backups/0.10.0-20260101T000000Z" ]] || fail "prune_old_backups() kept the chronologically OLDER backup (0.10.0, mtime 2026-01-01) instead of pruning it"
    log "  ✓ the chronologically newer backup (0.9.0) is kept even though it sorts first lexicographically"
}

# ---------------------------------------------------------------------------
# magpie-gc.timer stop/restart around the update window (Copilot finding):
# magpie-gc.timer is a SEPARATE unit from magpie.service -- untouched by
# backup_data()'s stop of the latter -- and its own `docker compose run
# magpie-ctl gc` can race a backup/swap in progress if it fires during that
# window. Must be stopped alongside magpie.service and restarted once the
# service is running again, on both the success and rollback paths.
# ---------------------------------------------------------------------------
test_backup_data_stops_gc_timer() {
    log "Test 17c: backup_data() stops magpie-gc.timer AND magpie-gc.service, not just magpie.service"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "gc_timer_stop")
    local calls_log="${TEST_DIR}/gc_timer_stop_calls.log"
    rm -f "$calls_log"

    (
        systemctl() { echo "$*" >> "$calls_log"; return 0; }
        INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="${install_dir}/backups/x" \
        NO_BACKUP="false" BACKUP_ARTIFACTS="skip" MAGPIE_VERSION="0.2.0" \
        backup_data
    ) >/dev/null 2>&1

    grep -qx "stop magpie.service" "$calls_log" || fail "backup_data() did not stop magpie.service: $(cat "$calls_log")"
    grep -qx "stop magpie-gc.timer" "$calls_log" || fail "backup_data() did not stop magpie-gc.timer -- it can fire mid-backup/swap and race the running copy: $(cat "$calls_log")"
    # magpie-gc.service is the oneshot GC job itself -- if the timer fired
    # moments before backup_data() ran, stopping only the timer leaves a
    # still-in-flight GC run free to keep mutating /data during the
    # backup/swap window; stopping the (Type=oneshot) service too
    # terminates it.
    grep -qx "stop magpie-gc.service" "$calls_log" || fail "backup_data() did not stop magpie-gc.service -- an in-flight GC run (triggered by the timer moments earlier) could still be mutating /data during the backup: $(cat "$calls_log")"
    log "  ✓ backup_data() stops magpie.service, magpie-gc.timer, AND magpie-gc.service"
}

test_rollback_and_cmd_update_restart_gc_timer() {
    log "Test 17d: rollback_to_prior() restarts magpie-gc.timer alongside magpie.service"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "gc_timer_restart" "OLD_DB_CONTENT")
    local backup_dir="${install_dir}/backups/gctimer"
    (
        systemctl() { return 0; }
        INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="$backup_dir" \
        HTTP_PORT=1 BIND_IP="" PRIOR_GIT_REF="" MAGPIE_VERSION="0.2.0" \
        NO_BACKUP="false" BACKUP_ARTIFACTS="skip"
        capture_prior_state
        backup_data
    ) >/dev/null 2>&1

    local calls_log="${TEST_DIR}/gc_timer_restart_calls.log"
    rm -f "$calls_log"
    (
        systemctl() { echo "$*" >> "$calls_log"; return 0; }
        wait_for_healthy() { return 0; }
        docker() { return 1; }
        INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="$backup_dir" \
        NO_BACKUP="false" PRIOR_MAGPIE_IMAGE="" PRIOR_GIT_REF="" \
        rollback_to_prior
    ) >/dev/null 2>&1

    grep -qx "start magpie.service" "$calls_log" || fail "rollback_to_prior() did not restart magpie.service: $(cat "$calls_log")"
    grep -qx "start magpie-gc.timer" "$calls_log" || fail "rollback_to_prior() did not restart magpie-gc.timer -- it would stay stopped after a rolled-back update: $(cat "$calls_log")"
    log "  ✓ rollback_to_prior() restarts both magpie.service and magpie-gc.timer"
}

# Regression test for a Copilot finding: rollback_to_prior()'s re-read of
# the restored .env for its own health re-assert only checked
# MAGPIE_BIND_IP, missing the legacy unprefixed BIND_IP key. Rolling back
# a pre-0.2.0 install's FIRST-EVER `update` restores a .env that may still
# only have the legacy key (reconcile_env_file_for_update() hadn't
# migrated it yet when this backup was taken) -- checking only the
# prefixed key would leave $BIND_IP at the failed update's (stale, wrong)
# value and probe the wrong address.
test_rollback_reasserts_using_legacy_bind_ip_key() {
    log "Test 17g: rollback_to_prior() falls back to the legacy BIND_IP key when the restored .env has no MAGPIE_BIND_IP"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "legacy_bind_ip" "OLD_DB_CONTENT" "BIND_IP=10.9.9.9")
    local backup_dir="${install_dir}/backups/legacybindip"
    (
        systemctl() { return 0; }
        INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="$backup_dir" \
        HTTP_PORT=1 BIND_IP="" PRIOR_GIT_REF="" MAGPIE_VERSION="0.2.0" \
        NO_BACKUP="false" BACKUP_ARTIFACTS="skip"
        capture_prior_state
        backup_data
    ) >/dev/null 2>&1

    local probed_url_file="${TEST_DIR}/legacy_bind_ip_probed_url"
    rm -f "$probed_url_file"
    (
        systemctl() { return 0; }
        wait_for_healthy() { health_check_url > "$probed_url_file"; return 0; }
        docker() { return 1; }
        INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="$backup_dir" \
        NO_BACKUP="false" PRIOR_MAGPIE_IMAGE="" PRIOR_GIT_REF="" \
        BIND_IP="1.2.3.4" \
        rollback_to_prior
    ) >/dev/null 2>&1

    local probed_url
    probed_url=$(cat "$probed_url_file" 2>/dev/null || echo "<not captured>")
    # Port 8080 comes from the restored .env's MAGPIE_HTTP_PORT (re-read
    # the same way as the BIND_IP fallback under test); the HTTP_PORT=1
    # set in the first subshell only scoped capture_prior_state/backup_data
    # above and has no bearing here.
    [[ "$probed_url" == "http://10.9.9.9:8080/health" ]] || fail "rollback_to_prior() did not fall back to the legacy BIND_IP key from the restored .env -- probed '$probed_url', expected the legacy value 10.9.9.9 (not the stale pre-rollback BIND_IP=1.2.3.4)"
    log "  ✓ rollback_to_prior() falls back to the legacy BIND_IP key when MAGPIE_BIND_IP is absent"
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
# capture_probe_state()'s token-parsing regex -- regression test for a bug
# a live-container run caught: auto-generated tokens use
# secrets.token_urlsafe()'s alphabet (alnum plus '-' and '_'), and a
# character class missing '-' silently truncates the parsed token at the
# first hyphen, producing a token-shaped-but-wrong value that then fails
# every A3 auth check. curl is shadowed to simulate a reachable prior
# install (only far enough to reach the token-create/parse step).
# ---------------------------------------------------------------------------
test_capture_probe_state_token_regex_handles_hyphens() {
    log "Test 20: capture_probe_state() does not truncate a probe token at a hyphen"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "token_regex")
    local hyphenated_token="mgp_ADMIN_tgYzFL9o1RLghLo8TXt-6s3gNXZ0os-W-J6JOkKcn2U"

    local captured
    captured=$(
        (
            curl() { return 0; }  # "prior install is reachable"
            compose_exec() {
                case "$*" in
                    *"token create"*)
                        cat << EOF
============================================================
TOKEN CREATED: magpie-update-probe-1234 (scope: admin)
Save this token - it will NOT be shown again!

${hyphenated_token}
============================================================
EOF
                        ;;
                    *"token list"*) echo "Total: 1 token(s)" ;;
                    *"migrate --check"*) echo "Data-format version: 0 (current: 1)" ;;
                    *"ls -r"*) echo "No artifacts found." ;;
                    *) return 1 ;;
                esac
            }
            INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" HTTP_PORT=1 BIND_IP="" \
            capture_probe_state >/dev/null 2>&1
            echo "$PROBE_TOKEN"
        )
    )
    [[ "$captured" == "$hyphenated_token" ]] || fail "PROBE_TOKEN was truncated or wrong: expected '$hyphenated_token', got '$captured'"
    log "  ✓ a hyphen-containing token is captured in full, not truncated"
}

# Regression test for a Copilot finding: capture_probe_state() used to
# capture PROBE_ARTIFACT_SHA256 from the SAME untagged `magpie info $path`
# query used to discover the tag list -- that query resolves via magpie's
# implicit ":latest" default, so the captured hash was really "whatever
# latest is," not explicitly the chosen PROBE_ARTIFACT_REF's hash (even
# though the two happen to coincide today, per the untagged query's own
# tags-are-for-the-resolved-hash semantics -- being explicit removes any
# reliance on that holding forever, and closes the gap where a FUTURE tag
# regression isolated to the non-latest tag wouldn't be caught). Confirms
# the fix: a SECOND, explicitly ref-qualified `info path:tag` call is made
# to get the hash, not just the bare untagged query.
test_capture_probe_state_queries_info_by_explicit_ref() {
    log "Test 20b: capture_probe_state() queries 'info path:tag' explicitly for the hash, not just the implicit-:latest untagged query"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "probe_explicit_ref")
    local calls_log="${TEST_DIR}/probe_explicit_ref_calls.log"
    rm -f "$calls_log"

    (
        curl() { return 0; }
        compose_exec() {
            echo "$*" >> "$calls_log"
            case "$*" in
                *"token create"*) echo "mgp_ADMIN_faketoken123" ;;
                *"token list"*) echo "Total: 1 token(s)" ;;
                *"migrate --check"*) echo "Data-format version: 0 (current: 1)" ;;
                *"ls -r"*) echo "myproj/myart" ;;
                *"info myproj/myart:v1"*)
                    printf 'Hash:        deadbeef123\nTags:        v1, latest\n'
                    ;;
                *"info myproj/myart"*)
                    printf 'Hash:        deadbeef123\nTags:        v1, latest\n'
                    ;;
                *) return 1 ;;
            esac
        }
        INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" HTTP_PORT=1 BIND_IP="" \
        capture_probe_state >/dev/null 2>&1
    )

    grep -qF "info myproj/myart:v1" "$calls_log" \
        || fail "capture_probe_state() never made an explicitly ref-qualified 'info path:tag' call -- it's still relying on the implicit :latest untagged query for the hash: $(cat "$calls_log")"
    log "  ✓ capture_probe_state() explicitly re-queries by tag for the hash"
}

# Regression test for a Copilot finding: capture_probe_state() parses the
# minted token via a `grep | head` pipeline inside a plain `VAR=$(...)`
# assignment. Under `set -euo pipefail`, if `grep` matches nothing (an
# unexpected `magpie-ctl` output format -- simulated here by a
# `token create` response with no `mgp_...` token in it at all), pipefail
# makes the pipeline's exit status nonzero, and a bare failing `VAR=$(...)`
# assignment aborts the whole script -- exactly wrong for what's supposed
# to be a best-effort capture.
test_capture_probe_state_token_parse_failure_survives_real_errexit() {
    log "Test 20c: capture_probe_state() survives a token-parse failure under real errexit (does not abort bare)"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "probe_parse_fail")

    local out
    out=$(
        (
            set -euo pipefail  # magpie-deploy.sh's own real semantics
            curl() { return 0; }
            compose_exec() {
                case "$*" in
                    *"token create"*) echo "unexpected output with no token in it" ;;
                    *"token list"*) echo "Total: 1 token(s)" ;;
                    *"migrate --check"*) echo "Data-format version: 0 (current: 1)" ;;
                    *"ls -r"*) echo "No artifacts found." ;;
                    *) return 1 ;;
                esac
            }
            INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" HTTP_PORT=1 BIND_IP="" \
            capture_probe_state
            echo "SURVIVED"
        ) 2>&1
    )
    local rc=$?

    [[ $rc -eq 0 ]] || fail "capture_probe_state() aborted (rc=$rc) instead of surviving a token-parse failure as best-effort: $out"
    echo "$out" | grep -q "SURVIVED" || fail "capture_probe_state() did not run to completion after a token-parse failure: $out"
    log "  ✓ a token-parse failure (grep finds nothing) does not abort the script under real errexit"
}

# ---------------------------------------------------------------------------
# Errexit-PRESERVED regression tests (adversarial review findings A1/A3).
#
# Every test above runs under this FILE's own `set +e` (asserted after
# sourcing, near the top of this file) -- which means the entire class of
# "an unguarded command aborts the whole script via raw set -e" bugs is
# structurally invisible to them: a bug that would abort a real `update`
# run mid-swap just... doesn't abort here either, and the test can't tell
# the difference between "handled gracefully" and "this test harness
# happens to have errexit off." These two tests explicitly re-enable
# `set -euo pipefail` (magpie-deploy.sh's own top-of-file semantics) INSIDE
# their subshells, so they exercise the real production failure mode.
# ---------------------------------------------------------------------------
test_cmd_update_mid_swap_failure_triggers_rollback_under_real_errexit() {
    log "Test 21: a real mid-swap failure, with errexit PRESERVED (not this file's set +e), reaches rollback_to_prior() rather than aborting bare"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "midswap_fail" "" "MAGPIE_HTTP_PORT=1")

    # A real (if minimal) git repository at INSTALL_DIR/repo -- no remote
    # configured (update_repo_to_latest(), which needs one, is stubbed out
    # below; it's pre-existing #582-covered logic, not part of this PR).
    # Deliberately has NO docker-compose.yml, so the swap's own
    # `cp "${INSTALL_DIR}/repo/docker-compose.yml" ...` step fails for
    # REAL -- not a simulated/stubbed failure -- which is exactly the
    # class of mid-swap failure issue #561's A1 finding was about (a
    # docker-pull network blip or git hiccup left the site down with
    # rollback never called).
    mkdir -p "${install_dir}/repo"
    (
        cd "${install_dir}/repo"
        git init -q
        git config user.email test@example.com
        git config user.name test
        # A local-only override for this throwaway fixture repo, not a
        # statement about real commit policy: the ambient global git
        # config on a dev machine may set commit.gpgsign (e.g. via
        # 1Password), which has nothing to do with this disposable,
        # never-pushed test repository and would otherwise fail the
        # commit below (and print a scary, harmless "failed to write
        # commit object" line) wherever that agent isn't reachable.
        git config commit.gpgsign false
        printf '[project]\nversion = "0.2.0"\n' > pyproject.toml
        git add pyproject.toml
        git commit -q -m init
    )

    local out
    out=$(
        (
            set -euo pipefail  # magpie-deploy.sh's own real semantics
            update_repo_to_latest() { :; }  # needs a real remote; out of scope for this test (see #582)
            systemctl() { return 0; }
            wait_for_healthy() { return 1; }  # fail fast rather than really retrying/probing
            docker() { return 1; }
            compose_exec() { return 1; }
            INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" NONINTERACTIVE="true" \
            ACCEPT_EMPTY_TRUSTED_PROXIES="true" GITHUB_BRANCH="default" \
            cmd_update
        ) 2>&1
    )
    local rc=$?
    [[ $rc -ne 0 ]] || fail "cmd_update() reported success despite a real mid-swap cp failure"
    echo "$out" | grep -qi "Failed to copy docker-compose.yml" || fail "the specific swap-step failure message was lost: $out"
    echo "$out" | grep -qi "Rolling back to the prior install" || fail "a real (errexit-preserved) mid-swap failure did NOT reach rollback_to_prior() -- this is issue #561's A1: the site would be left down with the old service stopped and no rollback. Output: $out"
    log "  ✓ a real mid-swap cp failure, with errexit preserved, reaches rollback_to_prior() -- not a bare abort leaving the site down"
}

# Regression test for a Copilot finding on the FIRST gc.timer fix: the
# original version restarted magpie-gc.timer immediately after
# `systemctl restart magpie.service`, BEFORE the post-update gate ran --
# so a subsequent assert failure routed to rollback_to_prior() with the
# timer already active, able to fire during rollback's OWN restore
# window. Reuses Test 21's real mid-swap-failure fixture, but logs every
# systemctl call to prove the ORDER is safe: no "start magpie-gc.timer"
# ever appears before rollback's own defensive "stop magpie-gc.timer".
test_gc_timer_never_started_before_rollback_stops_it_again() {
    log "Test 21b: magpie-gc.timer is never (re)started before rollback_to_prior() has a chance to stop it again"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "midswap_gctimer" "" "MAGPIE_HTTP_PORT=1")
    mkdir -p "${install_dir}/repo"
    (
        cd "${install_dir}/repo"
        git init -q
        git config user.email test@example.com
        git config user.name test
        git config commit.gpgsign false
        printf '[project]\nversion = "0.2.0"\n' > pyproject.toml
        git add pyproject.toml
        git commit -q -m init
    )

    local calls_log="${TEST_DIR}/gc_timer_order_calls.log"
    rm -f "$calls_log"
    (
        set -euo pipefail
        update_repo_to_latest() { :; }
        systemctl() { echo "$*" >> "$calls_log"; return 0; }
        wait_for_healthy() { return 1; }
        docker() { return 1; }
        compose_exec() { return 1; }
        INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" NONINTERACTIVE="true" \
        ACCEPT_EMPTY_TRUSTED_PROXIES="true" GITHUB_BRANCH="default" \
        cmd_update
    ) >/dev/null 2>&1

    [[ -s "$calls_log" ]] || fail "setup problem: no systemctl calls were logged at all"

    # The timer must never be started at any point before its SECOND stop
    # (rollback_to_prior()'s own defensive stop, which runs before the
    # restore). A "start magpie-gc.timer" appearing before that second
    # stop would mean it was active during rollback's restore window.
    local second_stop_line
    second_stop_line=$(grep -n '^stop magpie-gc\.timer$' "$calls_log" | sed -n '2p' | cut -d: -f1)
    [[ -n "$second_stop_line" ]] || fail "rollback_to_prior() never stopped magpie-gc.timer a second (defensive) time: $(cat "$calls_log")"

    local premature_start_line
    premature_start_line=$(grep -n '^start magpie-gc\.timer$' "$calls_log" | head -1 | cut -d: -f1)
    if [[ -n "$premature_start_line" && "$premature_start_line" -lt "$second_stop_line" ]]; then
        fail "magpie-gc.timer was started (line $premature_start_line) BEFORE rollback_to_prior()'s defensive stop (line $second_stop_line) -- it would have been active during the restore window: $(cat "$calls_log")"
    fi
    log "  ✓ magpie-gc.timer is never started before rollback_to_prior() has stopped it again"
}

# Regression test for a Copilot finding: in the --no-rollback failure
# path, the operator has made a deliberate, informed choice to leave the
# NEW (running) stack up for investigation -- it's a genuine live service
# now, not a "state needs hands-off caution" case like a failed rollback
# restore. magpie-gc.timer (stopped by backup_data() for the update
# window) must be restarted here too, not left off indefinitely.
test_no_rollback_restarts_gc_timer_before_dying() {
    log "Test 21c: --no-rollback restarts magpie-gc.timer before dying, leaving the new stack's GC schedule intact"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "no_rollback_gctimer" "" "MAGPIE_HTTP_PORT=1")

    # A real git repo with a docker-compose.yml this time, so the swap
    # itself succeeds and cmd_update() reaches the post-update assert gate
    # (which is what's under test here, not the swap).
    mkdir -p "${install_dir}/repo"
    (
        cd "${install_dir}/repo"
        git init -q
        git config user.email test@example.com
        git config user.name test
        git config commit.gpgsign false
        printf '[project]\nversion = "0.2.0"\n' > pyproject.toml
        echo "services: {}" > docker-compose.yml
        git add pyproject.toml docker-compose.yml
        git commit -q -m init
    )

    local calls_log="${TEST_DIR}/no_rollback_gctimer_calls.log"
    rm -f "$calls_log"
    (
        set -euo pipefail
        update_repo_to_latest() { :; }
        systemctl() { echo "$*" >> "$calls_log"; return 0; }
        wait_for_healthy() { return 1; }  # forces the post-update assert gate to fail
        docker() { return 0; }  # swap's docker pull/build succeeds
        compose_exec() { return 1; }
        INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" NONINTERACTIVE="true" \
        ACCEPT_EMPTY_TRUSTED_PROXIES="true" GITHUB_BRANCH="default" NO_ROLLBACK="true" \
        cmd_update
    ) >/dev/null 2>&1
    local rc=$?

    [[ $rc -ne 0 ]] || fail "cmd_update() reported success despite --no-rollback + a failed health check"
    grep -qx "start magpie-gc.timer" "$calls_log" \
        || fail "the --no-rollback path did not restart magpie-gc.timer before dying -- GC would stay off indefinitely on the intentionally-kept-running new stack: $(cat "$calls_log")"
    log "  ✓ --no-rollback restarts magpie-gc.timer before dying"
}

# Regression test for a Copilot finding: backup_data() runs BEFORE
# cmd_update()'s swap-window subshell exists to catch anything, so its own
# cp calls (copying magpie.db/.env/admin-token into the backup) were
# unguarded under this script's `set -euo pipefail` -- a failure there
# (disk full is the plausible cause) would abort the whole script raw,
# leaving the just-stopped magpie.service down with no rollback engaged.
test_backup_data_cp_failure_triggers_rollback_under_real_errexit() {
    log "Test 17e: a real backup_data() cp failure, with errexit PRESERVED, reaches rollback_to_prior() rather than aborting bare"

    if [[ "$(id -u)" -eq 0 ]]; then
        log "  (skipped: running as root -- cannot simulate a permission-denied cp failure via chmod)"
        return 0
    fi

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "backup_cp_fail" "OLD_DB_CONTENT")
    # Force a REAL cp failure: the source magpie.db is unreadable, so
    # backup_data()'s very first cp fails for real (permission denied).
    chmod 000 "${data_dir}/magpie.db"

    local out
    out=$(
        (
            set -euo pipefail  # magpie-deploy.sh's own real semantics
            systemctl() { return 0; }
            wait_for_healthy() { return 0; }
            docker() { return 1; }
            INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="${install_dir}/backups/x" \
            HTTP_PORT=1 BIND_IP="" PRIOR_GIT_REF="" MAGPIE_VERSION="0.2.0" \
            NO_BACKUP="false" BACKUP_ARTIFACTS="skip"
            capture_prior_state
            preflight_backup_space
            backup_data
        ) 2>&1
    )
    local rc=$?
    chmod 644 "${data_dir}/magpie.db" 2>/dev/null || true  # restore perms so TEST_DIR cleanup can remove it

    [[ $rc -ne 0 ]] || fail "backup_data() reported success despite a real cp failure backing up magpie.db"
    echo "$out" | grep -qi "Failed to back up magpie.db" || fail "the specific backup-step failure message was lost: $out"
    echo "$out" | grep -qi "Rolling back to the prior install" || fail "a real (errexit-preserved) backup_data() cp failure did NOT reach rollback_to_prior() -- the just-stopped service would be left down with no rollback. Output: $out"
    log "  ✓ a real backup_data() cp failure, with errexit preserved, reaches rollback_to_prior() -- not a bare abort leaving the site down"
}

test_rollback_cp_failure_dies_loudly_under_real_errexit() {
    log "Test 22: a real cp failure during rollback's own restore, with errexit PRESERVED, dies loudly with the backup path rather than aborting bare"

    if [[ "$(id -u)" -eq 0 ]]; then
        log "  (skipped: running as root -- cannot simulate a permission-denied cp failure via chmod)"
        return 0
    fi

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "rollback_cp_fail" "OLD_DB_CONTENT")
    local backup_dir="${install_dir}/backups/cpfail"

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
    [[ -f "${backup_dir}/data/magpie.db" ]] || fail "setup problem: backup_data() did not produce a DB backup to roll back from"

    # Force a REAL cp failure (permission denied), not a simulated one.
    chmod 000 "${backup_dir}/data/magpie.db"

    local out
    out=$(
        (
            set -euo pipefail  # magpie-deploy.sh's own real semantics
            systemctl() { return 0; }
            wait_for_healthy() { return 0; }
            docker() { return 1; }
            INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="$backup_dir" \
            NO_BACKUP="false" PRIOR_MAGPIE_IMAGE="" PRIOR_GIT_REF="" \
            rollback_to_prior
        ) 2>&1
    )
    local rc=$?
    chmod 644 "${backup_dir}/data/magpie.db" 2>/dev/null || true  # restore perms so TEST_DIR cleanup can remove it

    [[ $rc -ne 0 ]] || fail "rollback_to_prior() reported success despite a real cp failure restoring magpie.db"
    echo "$out" | grep -qi "Rollback restore itself FAILED" || fail "a real (errexit-preserved) restore cp failure did NOT reach rollback_to_prior()'s own loud die() -- this is issue #561's A3: it would abort bare instead of the promised 'always exits via die()' contract. Output: $out"
    echo "$out" | grep -qF "$backup_dir" || fail "the loud restore-failure die() did not include the backup path for manual recovery: $out"
    log "  ✓ a real restore cp failure, with errexit preserved, dies loudly with the backup path -- not a bare abort"
}

# ---------------------------------------------------------------------------
# rollback_to_prior()'s systemd-unit restore loop short-circuits on the
# FIRST failed unit, rather than pressing on and leaving systemd in a
# partially-restored state (some units on the prior version, some still
# on the failed update's).
# ---------------------------------------------------------------------------
test_rollback_unit_restore_short_circuits_on_first_failure() {
    log "Test 23: rollback_to_prior()'s systemd-unit restore loop stops at the first failed unit, not all three"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "unit_short_circuit")
    local backup_dir="${install_dir}/backups/unitfail"
    mkdir -p "${backup_dir}/rollback" "${backup_dir}/data"
    # Fabricated directly (not via capture_prior_state(), which only
    # snapshots a unit that already exists at the real
    # /etc/systemd/system/${unit} -- not present in this sandbox) so all
    # three units are present for rollback_to_prior() to attempt, in a
    # known order.
    for unit in magpie.service magpie-gc.service magpie-gc.timer; do
        echo "fake ${unit} content" > "${backup_dir}/rollback/${unit}"
    done
    printf '%s\n' "OLD_DB_CONTENT" > "${backup_dir}/data/magpie.db"

    local calls_log="${TEST_DIR}/unit_short_circuit_cp_calls"
    rm -f "$calls_log"
    (
        systemctl() { return 0; }
        wait_for_healthy() { return 1; }
        docker() { return 1; }
        # Shadows the real `cp` (an external command, so a same-shell
        # function overrides it same as systemctl/docker above): logs
        # every systemd-unit-destined call and fails the FIRST one
        # (magpie.service) as if that single cp hit a real error --
        # everything else passes through to the real cp unmodified.
        cp() {
            if [[ "$*" == *"/etc/systemd/system/"* ]]; then
                echo "$*" >> "$calls_log"
                [[ "$*" == *"/etc/systemd/system/magpie.service"* ]] && return 1
            fi
            command cp "$@"
        }
        INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" BACKUP_DIR="$backup_dir" \
        NO_BACKUP="false" PRIOR_MAGPIE_IMAGE="" PRIOR_GIT_REF="" \
        rollback_to_prior
    ) >/dev/null 2>&1
    local rc=$?

    [[ $rc -ne 0 ]] || fail "rollback_to_prior() reported success despite a failed unit restore"
    local unit_call_count
    unit_call_count=$(wc -l < "$calls_log" 2>/dev/null || echo 0)
    [[ "$unit_call_count" -eq 1 ]] || fail "rollback_to_prior() attempted ${unit_call_count} systemd-unit restores after the first one failed -- expected exactly 1 (magpie.service), not a continued attempt at magpie-gc.service/magpie-gc.timer. Calls: $(cat "$calls_log" 2>/dev/null)"
    grep -q "magpie.service" "$calls_log" || fail "the one attempted unit restore wasn't magpie.service: $(cat "$calls_log" 2>/dev/null)"
    log "  ✓ the loop stops after magpie.service fails -- magpie-gc.service/magpie-gc.timer are never attempted"
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
test_compute_backup_dir_rejects_unsafe_version
test_preflight_backup_space
test_backup_data_snapshots_db_env_and_writes_manifest
test_backup_data_refuses_symlinked_admin_token
test_resolve_admin_token_host_path_rejects_traversal
test_backup_data_artifacts_modes
test_backup_data_no_backup_skips_everything
test_capture_prior_state_snapshots_config_and_skips_unreachable_probes
test_capture_prior_state_cp_failure_dies_clearly_under_real_errexit
test_assert_post_update_a1_short_circuits_on_unhealthy
test_assert_post_update_skips_a2_a3_a4_when_nothing_captured
test_assert_post_update_a4_queries_info_by_explicit_ref
test_assert_post_update_a5_fails_on_version_regression
test_assert_post_update_a6_fails_on_row_count_change
test_assert_post_update_a5_survives_total_compose_exec_failure
test_assert_post_update_a6_survives_total_compose_exec_failure
test_assert_post_update_a2_survives_total_compose_exec_failure
test_rollback_fires_and_restores_prior_state
test_rollback_reports_loudly_when_restore_itself_unhealthy
test_rollback_revokes_probe_token_after_healthy_restore
test_prune_old_backups_keeps_newest_n
test_prune_old_backups_noop_under_the_limit
test_prune_old_backups_sorts_by_mtime_not_name
test_backup_data_stops_gc_timer
test_backup_data_cp_failure_triggers_rollback_under_real_errexit
test_rollback_and_cmd_update_restart_gc_timer
test_rollback_reasserts_using_legacy_bind_ip_key
test_run_data_migration_failure_propagates
test_capture_probe_state_token_regex_handles_hyphens
test_capture_probe_state_queries_info_by_explicit_ref
test_capture_probe_state_token_parse_failure_survives_real_errexit
test_cmd_update_mid_swap_failure_triggers_rollback_under_real_errexit
test_gc_timer_never_started_before_rollback_stops_it_again
test_no_rollback_restarts_gc_timer_before_dying
test_rollback_cp_failure_dies_loudly_under_real_errexit
test_rollback_unit_restore_short_circuits_on_first_failure
test_cmd_update_validates_envelope_flags

log ""
log "All tests passed!"
