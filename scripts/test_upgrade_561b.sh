#!/bin/bash
# Real end-to-end release-gate test for issue #561 (the "canonical upgrade
# test" scoped as #561-B): a genuine v0.1.6 two-container install, upgraded
# in place to v0.2.0+'s bundled single container via each version's OWN
# scripts/magpie-deploy.sh, against the real published ghcr.io images --
# plus a fault-injection run proving the backup/assert/rollback envelope
# (issue #561, PR #600) actually restores a working prior stack with data
# intact when the update fails partway through.
#
# This is deliberately NOT wired into `just e2e`/CI: it is root-required
# (systemd units, /opt), mutates real host state, and needs real network
# access to github.com and ghcr.io for two full image pulls. Run it by
# hand, one instance at a time, on a host with no existing magpie install:
#
#   MAGPIE_RUN_LIVE_UPGRADE_TEST=1 scripts/test_upgrade_561b.sh
#
# Optional: --only-upgrade / --only-rollback to run a single scenario
# while iterating.
#
# The installer hardcodes a single, non-namespaced `magpie.service` unit --
# only one magpie install can exist on a host at a time. This script
# refuses to start if one is already present (see preflight_host_clean),
# and unconditionally tears its own install down (systemd units,
# containers, install dir) on every exit path, success or failure.
#
# Fault injection (the rollback scenario) does NOT touch any shared host
# resource (no /etc/hosts, no daemon.json, no iptables): it points
# DOCKER_CONFIG at a throwaway credential-helper config for just the one
# `update` invocation, which makes the docker CLI fail client-side,
# quickly and deterministically, resolving credentials for ghcr.io --
# without affecting any other process's docker usage on this host. This
# lands on the same swap-window failure the code's own comments call out
# ("a docker-pull network blip... previously left the stack stopped with
# no rollback"), just reproduced safely instead of an actual outage.

set -uo pipefail

log() { printf '[test-561b] %s\n' "$*"; }
log_error() { printf '[test-561b] ERROR: %s\n' "$*" >&2; }

# REPO_ROOT/SCRATCH_DIR are checked explicitly, not just assigned via
# unguarded command substitution: this script is root-required and
# host-mutating (seed files, the extracted v0.1.6 installer, and later a
# `sudo rm -rf "$INSTALL_DIR"` all key off these paths), and `set -u`
# alone does NOT catch a command substitution that fails but still
# produces empty output -- a failed `mktemp` or `cd`/`pwd` would otherwise
# silently leave SCRATCH_DIR/REPO_ROOT empty, and every `${SCRATCH_DIR}/...`
# path built below would then resolve to a bare filesystem root path
# instead of erroring.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "$REPO_ROOT" || "$REPO_ROOT" != /* ]]; then
    log_error "Could not resolve this script's own repo root (cd/pwd failed or returned something unexpected: '${REPO_ROOT}')."
    exit 1
fi
SCRATCH_DIR="$(mktemp -d /tmp/magpie-561b-test.XXXXXX)"
if [[ -z "$SCRATCH_DIR" || "$SCRATCH_DIR" != /tmp/magpie-561b-test.* ]]; then
    log_error "mktemp failed to create a scratch directory (got: '${SCRATCH_DIR}') -- refusing to continue rather than risk writing seed files/the extracted installer to an unexpected path."
    exit 1
fi
V016_SCRIPT="${SCRATCH_DIR}/magpie-deploy-v0.1.6.sh"
NEW_SCRIPT="${REPO_ROOT}/scripts/magpie-deploy.sh"

# Isolated from the retired /opt/magpie and from any other agent's install
# -- see this script's header comment on the single-non-namespaced-unit
# constraint.
INSTALL_DIR="/opt/magpie-561b-test"
DATA_DIR="${INSTALL_DIR}/data"
HTTP_PORT="18180"
TRUSTED_PROXIES="10.20.30.1"     # a plausible single upstream-proxy hop, not a broad range
ALLOWED_CIDRS="10.0.0.0/8"       # SWCCDC-prod-shape CIDR allow, see issue #561's scope doc row 1

RESULTS=()   # "feature|status|notes" rows for the final report

# $3 (notes) is frequently raw command output from callers (installer
# stderr, docker compose output, JSON blobs) -- sanitized here, once, so
# every call site is protected automatically: a literal '|' would
# otherwise be misread as this function's own field delimiter by
# print_report()'s `IFS='|' read`, and a raw newline would break out of
# the markdown table row entirely.
record() {
    local notes="${3//$'\n'/ }"
    notes="${notes//|/;}"
    RESULTS+=("$1|$2|${notes}")
}

# =============================================================================
# Preflight / teardown
# =============================================================================

# Fails loudly if the docker CLI cannot be queried at all (permissions,
# group membership not yet applied, daemon not running). Called before any
# existing-install check below: a swallowed `docker ps` failure would
# otherwise read as "no containers found" (an empty grep match under
# `2>/dev/null`) and let this script sail into a destructive install on a
# host it never actually managed to inspect.
require_docker_usable() {
    if ! docker ps >/dev/null 2>&1; then
        log_error "'docker ps' failed -- Docker is not usable in this session (check group membership/permissions, and that the daemon is running). Refusing to proceed: this script cannot safely check for -- or clean up -- an existing magpie install without a working docker CLI."
        exit 1
    fi
}

# Same reasoning as require_docker_usable() above, for `systemctl`: a
# non-systemd host or a dbus/permissions problem would otherwise make
# `systemctl list-units` fail, and a swallowed failure there reads exactly
# like "no magpie units" (an empty grep match under `2>/dev/null`).
require_systemctl_usable() {
    if ! systemctl list-units >/dev/null 2>&1; then
        log_error "'systemctl list-units' failed -- systemd is not usable in this session (non-systemd host, or a dbus/permissions problem). Refusing to proceed: this script cannot safely check for -- or clean up -- an existing magpie systemd install."
        exit 1
    fi
}

preflight_host_clean() {
    require_docker_usable
    require_systemctl_usable

    local dirty=0
    # Captured first, then grepped, for the same reason as the docker_ps_out
    # pattern below: piping `systemctl list-units ... | grep -qi ...`
    # directly would make a `systemctl` failure indistinguishable from "no
    # magpie units" under `pipefail`. require_systemctl_usable() above
    # already confirmed the CLI works; still checked explicitly here
    # rather than assumed.
    local systemctl_out
    if ! systemctl_out="$(systemctl list-units --all 2>&1)"; then
        log_error "'systemctl list-units --all' failed after require_systemctl_usable() reported systemd usable -- refusing to proceed. Output: ${systemctl_out}"
        exit 1
    fi
    if echo "$systemctl_out" | grep -qi 'magpie'; then
        log_error "A magpie systemd unit already exists on this host -- refusing to start (only one install can exist at a time)."
        echo "$systemctl_out" | grep -i magpie >&2
        dirty=1
    fi
    # Captured first, then grepped -- NOT `docker ps ... | grep -qi ...`
    # directly, which under `pipefail` would make a `docker ps` failure
    # indistinguishable from "no magpie containers" (both leave the `if`
    # false). require_docker_usable() above already confirmed the CLI
    # works, so this capture is expected to succeed; still checked
    # explicitly rather than assumed.
    local docker_ps_out
    if ! docker_ps_out="$(docker ps -a --format '{{.Names}}' 2>&1)"; then
        log_error "'docker ps -a' failed after require_docker_usable() reported Docker usable -- refusing to proceed. Output: ${docker_ps_out}"
        exit 1
    fi
    if echo "$docker_ps_out" | grep -qi 'magpie'; then
        log_error "A magpie-named container already exists on this host -- refusing to start."
        echo "$docker_ps_out" | grep -i magpie >&2
        dirty=1
    fi
    if [[ -e "$INSTALL_DIR" ]]; then
        log_error "${INSTALL_DIR} already exists -- refusing to start (would not be this test's own clean install)."
        dirty=1
    fi
    if (( dirty != 0 )); then
        exit 1
    fi
    log "Preflight OK: host has no existing magpie units, containers, or ${INSTALL_DIR}."
}

# Tears down this test's own magpie install (systemd units, containers,
# INSTALL_DIR). Never uses a wildcard/filter-based docker cleanup -- every
# removal is scoped to this test's own known INSTALL_DIR/compose file.
# Does NOT touch SCRATCH_DIR -- that holds the extracted v0.1.6 installer
# and seed files this scenario is still using, and is only removed once,
# by full_teardown() below, when the whole script exits. Idempotent and
# safe to call multiple times (defensively, between scenarios, and again
# on exit).
teardown_host() {
    log "Tearing down (${INSTALL_DIR})..."

    # Undoing the fault-injection DOCKER_CONFIG trick has no host-side
    # state to unwind (it was only ever an env var on one subprocess) --
    # nothing to do here for that.

    # Guard against ever touching a magpie.service that isn't THIS test's
    # own: the installer hardcodes this exact, non-namespaced unit name
    # (see this script's header comment), so a real, unrelated magpie
    # install on this host would use the identical name. Its
    # WorkingDirectory=/EnvironmentFile= lines both key off INSTALL_DIR
    # (see generate_systemd_service() in magpie-deploy.sh), so grepping
    # the unit file for our own INSTALL_DIR is a reliable "is this ours"
    # check. Absent unit file -> nothing to protect, proceeds normally.
    # Present but NOT ours -> refuse to touch it and leave it running.
    local unit_file="/etc/systemd/system/magpie.service"
    local unit_is_ours="true"
    if [[ -f "$unit_file" ]] && ! grep -qF "$INSTALL_DIR" "$unit_file" 2>/dev/null; then
        unit_is_ours="false"
        log_error "Refusing to touch ${unit_file} -- it exists but does not reference this test's INSTALL_DIR (${INSTALL_DIR}). This looks like a REAL magpie install, not this test's own; leaving it running untouched."
    fi

    if [[ "$unit_is_ours" == "true" ]]; then
        if [[ -f "${INSTALL_DIR}/repo/scripts/magpie-deploy.sh" ]]; then
            sudo bash "${INSTALL_DIR}/repo/scripts/magpie-deploy.sh" uninstall \
                --install-dir "$INSTALL_DIR" --purge --yes >/dev/null 2>&1 || true
        fi

        sudo systemctl disable --now magpie.service magpie-gc.timer magpie-gc.service >/dev/null 2>&1 || true
        sudo rm -f /etc/systemd/system/magpie.service /etc/systemd/system/magpie-gc.service /etc/systemd/system/magpie-gc.timer
        sudo systemctl daemon-reload >/dev/null 2>&1 || true
    fi

    # Belt-and-suspenders: uninstall --purge above already removes
    # containers/volumes (scoped to INSTALL_DIR's own compose file) and
    # both DATA_DIR and INSTALL_DIR -- this repeats the compose teardown
    # by exact file reference (never a bare `docker compose down`, never a
    # name filter) in case uninstall itself didn't get far enough to run
    # it, then removes the directory outright.
    if [[ -f "${INSTALL_DIR}/docker-compose.yml" ]]; then
        docker compose -f "${INSTALL_DIR}/docker-compose.yml" --env-file "${INSTALL_DIR}/etc/.env" \
            down --volumes --remove-orphans >/dev/null 2>&1 || true
    fi
    sudo rm -rf "$INSTALL_DIR"

    local still_dirty=0
    # Same false-clean risk as the docker_ps_out capture below: a failed
    # `systemctl list-units` must count as "couldn't verify" (dirty), not
    # silently read as "no magpie units" the way a bare
    # `systemctl ... 2>/dev/null | grep -qi ...` would under pipefail.
    local systemctl_out
    if ! systemctl_out="$(systemctl list-units --all 2>&1)"; then
        log_error "Teardown verification FAILED: 'systemctl list-units --all' itself failed, so unit state could not be confirmed clean -- do not treat this as a clean host. Output: ${systemctl_out}"
        still_dirty=1
    elif echo "$systemctl_out" | grep -qi 'magpie'; then
        log_error "Teardown incomplete: a magpie systemd unit is still present."
        still_dirty=1
    fi
    # Captured, then checked, then grepped -- a `docker ps` failure here
    # (daemon restarted mid-run, permissions changed) must NOT read as "no
    # magpie containers found" the way `docker ps ... 2>/dev/null | grep
    # -qi ...` would under pipefail (a failed left side of the pipe still
    # leaves the right side's "no match" exit status, which the `if` can't
    # tell apart from a genuinely clean host). The whole point of this
    # check is guaranteeing no leftover magpie state -- "couldn't verify"
    # must count as dirty, never as clean.
    local docker_ps_out
    if ! docker_ps_out="$(docker ps -a --format '{{.Names}}' 2>&1)"; then
        log_error "Teardown verification FAILED: 'docker ps -a' itself failed, so container state could not be confirmed clean -- do not treat this as a clean host. Output: ${docker_ps_out}"
        still_dirty=1
    elif echo "$docker_ps_out" | grep -qi 'magpie'; then
        log_error "Teardown incomplete: a magpie-named container is still present."
        still_dirty=1
    fi
    if [[ -e "$INSTALL_DIR" ]]; then
        log_error "Teardown incomplete: ${INSTALL_DIR} still exists."
        still_dirty=1
    fi
    if (( still_dirty == 0 )); then
        log "Teardown verified clean: no magpie units, no magpie containers, ${INSTALL_DIR} removed."
    fi
    return $still_dirty
}

full_teardown() {
    # $? here is the exit status the script is about to use (whatever was
    # passed to `exit`, or the last command's status if the script fell
    # off the end) -- captured before teardown_host()/rm can change it.
    # If the rest of the run otherwise reported success (0) but teardown
    # verification itself reports a dirty/unverifiable host, that failure
    # must not be silently swallowed: this script's whole contract is
    # verifying the host is left clean on every exit path, so a dirty
    # host at exit time overrides an otherwise-successful status. An
    # ALREADY-nonzero incoming status (a real test failure) is left as
    # is -- it's already reporting failure; no need to change it.
    local incoming_rc=$?
    teardown_host
    local teardown_rc=$?
    rm -rf "$SCRATCH_DIR"
    if (( teardown_rc != 0 && incoming_rc == 0 )); then
        log_error "Teardown verification failed on exit -- overriding an otherwise-successful run. See the teardown errors above."
        exit 1
    fi
}

trap full_teardown EXIT

# =============================================================================
# Helpers
# =============================================================================

compose_exec() {
    docker compose -f "${INSTALL_DIR}/docker-compose.yml" --env-file "${INSTALL_DIR}/etc/.env" exec -T "$@"
}

# Prints the sorted, comma-joined list of docker-compose service names for
# INSTALL_DIR's compose file on success. On failure (a bad .env, permissions
# issue, missing compose plugin -- not just "no services"), returns
# non-zero with the command's own stderr on stdout instead, so every
# topology check below can report the REAL cause rather than a confusing
# "got: " (empty) mismatch that silently swallowed a `docker compose`
# error.
compose_service_list() {
    local out
    if ! out="$(docker compose -f "${INSTALL_DIR}/docker-compose.yml" --env-file "${INSTALL_DIR}/etc/.env" ps --services 2>&1)"; then
        echo "$out"
        return 1
    fi
    echo "$out" | sort | tr '\n' ','
}

wait_health() {
    local tries=0
    while (( tries < 60 )); do
        # --max-time bounds each individual request -- without it, a
        # stalled connect/response (a wedged network stack, a half-dead
        # container) could hang this curl indefinitely despite the retry
        # loop's own counter, defeating the point of a bounded wait.
        curl -sf --max-time 5 "http://127.0.0.1:${HTTP_PORT}/health" >/dev/null 2>&1 && return 0
        sleep 2
        tries=$(( tries + 1 ))
    done
    return 1
}

# Host-side client, run from this checkout against the published port --
# the same vantage point a real operator's own machine has, exercising the
# full path (through Caddy, in the two-container topology) rather than
# reaching directly into the container.
magpie_client() {
    local token="$1"
    shift
    (cd "$REPO_ROOT" && MAGPIE_SERVER="http://127.0.0.1:${HTTP_PORT}" MAGPIE_TOKEN="$token" uv run magpie --format json "$@")
}

read_admin_token() {
    sudo cat "${DATA_DIR}/admin-token" 2>/dev/null
}

# =============================================================================
# Scenario steps
# =============================================================================

install_v016() {
    log "Extracting v0.1.6's own installer from the repo history..."
    # Checked explicitly -- an unfetched tag (e.g. a shallow clone) makes
    # `git show` fail, and an unchecked redirect would still write an
    # empty/partial $V016_SCRIPT that only surfaces as a much less
    # actionable failure several steps later (a mysterious `install`
    # error, or a permission-denied on a 0-byte "script"). stderr is
    # captured separately (not merged into $V016_SCRIPT) so a failure
    # neither pollutes the script file nor loses git's own error text.
    local git_show_err="${SCRATCH_DIR}/git-show-v016.err"
    if ! git -C "$REPO_ROOT" show v0.1.6:scripts/magpie-deploy.sh > "$V016_SCRIPT" 2>"$git_show_err"; then
        record "v0.1.6 install" "FAIL" "could not extract scripts/magpie-deploy.sh from the v0.1.6 tag (git show failed: $(cat "$git_show_err" 2>/dev/null); if this is a shallow clone, fetch the tag first: git -C ${REPO_ROOT} fetch --tags)"
        return 1
    fi
    if [[ ! -s "$V016_SCRIPT" ]]; then
        record "v0.1.6 install" "FAIL" "extracted v0.1.6 installer (${V016_SCRIPT}) is empty"
        return 1
    fi
    chmod +x "$V016_SCRIPT"

    log "Installing magpie v0.1.6 (two-container, tls-mode off, fronted CIDR shape)..."
    if ! sudo bash "$V016_SCRIPT" install \
        --release v0.1.6 \
        --noninteractive \
        --install-dir "$INSTALL_DIR" \
        --data-dir "$DATA_DIR" \
        --tls-mode off \
        --http-port "$HTTP_PORT" \
        --trusted-proxies "$TRUSTED_PROXIES"; then
        record "v0.1.6 install" "FAIL" "installer exited non-zero"
        return 1
    fi

    local services
    if ! services="$(compose_service_list)"; then
        record "v0.1.6 install" "FAIL" "'docker compose ps --services' failed: ${services}"
        return 1
    fi
    if [[ "$services" != "caddy,magpie," ]]; then
        record "v0.1.6 install" "FAIL" "expected two services (caddy,magpie), got: ${services}"
        return 1
    fi

    # MAGPIE_ALLOWED_CIDRS is never written by the installer (only ever
    # read) -- an operator hand-adds it to etc/.env, same as the real
    # SWCCDC prod shape. Append-then-restart to pick it up. Both checked
    # explicitly: under `set -uo pipefail` (no `-e`), a failed `sudo tee`
    # (permissions, full disk) or `systemctl restart` would otherwise be
    # silently ignored, and the run would continue without actually
    # exercising the intended fronted-CIDR shape at all.
    if ! echo "MAGPIE_ALLOWED_CIDRS=${ALLOWED_CIDRS}" | sudo tee -a "${INSTALL_DIR}/etc/.env" >/dev/null; then
        record "v0.1.6 install" "FAIL" "could not append MAGPIE_ALLOWED_CIDRS to ${INSTALL_DIR}/etc/.env"
        return 1
    fi
    if ! sudo systemctl restart magpie.service; then
        record "v0.1.6 install" "FAIL" "'systemctl restart magpie.service' failed after adding MAGPIE_ALLOWED_CIDRS"
        return 1
    fi
    if ! wait_health; then
        record "v0.1.6 install" "FAIL" "did not become healthy after adding MAGPIE_ALLOWED_CIDRS"
        return 1
    fi

    record "v0.1.6 install" "PASS" "two-container topology healthy on :${HTTP_PORT}"
    return 0
}

# Seeds real data and captures the "before" state this scenario will
# compare against after the update. Populates the SEED_* globals used by
# assert_data_continuity() below.
SEED_PATHS=()      # artifact paths
SEED_HASHES=()     # sha256 recorded at push time (server-reported)
SEED_TAG_PATH=""
SEED_TAG_NAME="stable-561b"
SEED_TAG_HASH=""
READ_TOKEN=""

seed_data() {
    # Reset from any prior scenario in this same process -- when both
    # scenarios run back to back (the default, no --only-* flag), each
    # gets a brand-new v0.1.6 install with brand-new random artifact
    # content, so a stale entry from the previous scenario would compare
    # against data that no longer exists on the (now torn down) install
    # that produced it.
    SEED_PATHS=()
    SEED_HASHES=()
    SEED_TAG_PATH=""
    SEED_TAG_HASH=""
    READ_TOKEN=""

    local admin_token
    admin_token="$(read_admin_token)"
    if [[ -z "$admin_token" ]]; then
        record "seed data" "FAIL" "could not read admin token from ${DATA_DIR}/admin-token"
        return 1
    fi
    ADMIN_TOKEN="$admin_token"

    log "Seeding artifacts (varied sizes)..."
    local names=("small" "medium" "large")
    local sizes=(4096 1048576 10485760)
    local i path tmpfile push_out hash actual_size
    for i in 0 1 2; do
        path="test/561b-${names[$i]}"
        tmpfile="${SCRATCH_DIR}/${names[$i]}.bin"
        # Checked explicitly, not just generated and trusted: under this
        # script's `set -uo pipefail` (no `-e`), an I/O failure (disk
        # full) could otherwise silently leave an empty/partial seed
        # file, which would still "pass" the later byte-identity check --
        # it would just be verifying the wrong (truncated) content against
        # itself, not proving anything about the real artifact.
        if ! head -c "${sizes[$i]}" /dev/urandom > "$tmpfile"; then
            record "seed data" "FAIL" "could not generate seed file ${tmpfile} (${sizes[$i]} bytes)"
            return 1
        fi
        actual_size="$(stat -c%s "$tmpfile" 2>/dev/null || echo 0)"
        if [[ "$actual_size" != "${sizes[$i]}" ]]; then
            record "seed data" "FAIL" "seed file ${tmpfile} is ${actual_size} bytes, expected ${sizes[$i]}"
            return 1
        fi
        push_out="$(magpie_client "$ADMIN_TOKEN" push "$tmpfile" --to "$path" 2>&1)"
        hash="$(echo "$push_out" | jq -r '.data.hash // empty' 2>/dev/null)"
        if [[ -z "$hash" ]]; then
            record "seed data" "FAIL" "push of ${path} did not return a hash: ${push_out}"
            return 1
        fi
        SEED_PATHS+=("$path")
        SEED_HASHES+=("$hash")
        log "  pushed ${path} (${sizes[$i]} bytes) -> ${hash:0:12}..."
    done

    # A mutable tag on one artifact, per task step 2 ("create tags").
    SEED_TAG_PATH="${SEED_PATHS[0]}"
    SEED_TAG_HASH="${SEED_HASHES[0]}"
    if ! magpie_client "$ADMIN_TOKEN" tag "${SEED_TAG_PATH}:latest" --as "$SEED_TAG_NAME" >/dev/null 2>&1; then
        record "seed data" "FAIL" "could not create tag ${SEED_TAG_NAME} on ${SEED_TAG_PATH}"
        return 1
    fi

    # A non-admin (read-scope) probe token, minted the documented way
    # (docker compose exec magpie-ctl, not the client CLI).
    local token_out
    token_out="$(compose_exec magpie magpie-ctl --format json token create --name reader-561b --scope read 2>&1)"
    READ_TOKEN="$(echo "$token_out" | jq -r '.data.token // empty' 2>/dev/null)"
    if [[ -z "$READ_TOKEN" ]]; then
        record "seed data" "FAIL" "could not mint non-admin token: ${token_out}"
        return 1
    fi
    if ! magpie_client "$READ_TOKEN" ls "test/" >/dev/null 2>&1; then
        record "seed data" "FAIL" "freshly minted non-admin token does not authenticate before the update"
        return 1
    fi

    record "seed data" "PASS" "${#SEED_PATHS[@]} artifacts, 1 tag (${SEED_TAG_NAME}), 1 non-admin token, all verified pre-update"
    return 0
}

# Verifies every seeded artifact retrieves byte-identically, the tag still
# resolves to the right hash, and the non-admin token still authenticates.
# Independent of (does not call into) the installer's own internal
# assert_post_update() -- this is the test's own outside-in proof.
assert_data_continuity() {
    local ok=1
    local i path expected_hash outfile got_hash
    for i in "${!SEED_PATHS[@]}"; do
        path="${SEED_PATHS[$i]}"
        expected_hash="${SEED_HASHES[$i]}"
        outfile="${SCRATCH_DIR}/verify-${i}.bin"
        rm -f "$outfile"
        if ! magpie_client "$ADMIN_TOKEN" get "$path" -o "$outfile" >/dev/null 2>&1; then
            log_error "  get ${path} failed"
            ok=0
            continue
        fi
        got_hash="$(sha256sum "$outfile" | cut -d' ' -f1)"
        # Server "hash" field may be a prefix or the full digest depending
        # on API version -- compare on the shared prefix length so this
        # works across both.
        local n="${#expected_hash}"
        if [[ "${got_hash:0:n}" != "$expected_hash" ]]; then
            log_error "  ${path}: downloaded sha256 ${got_hash} does not match recorded ${expected_hash}"
            ok=0
        else
            log "  ${path}: byte-identical (sha256 ${got_hash:0:12}...)"
        fi
    done

    local tag_out tag_hash
    tag_out="$(magpie_client "$ADMIN_TOKEN" info "${SEED_TAG_PATH}:${SEED_TAG_NAME}" 2>&1)"
    tag_hash="$(echo "$tag_out" | jq -r '.data.hash // empty' 2>/dev/null)"
    local n="${#SEED_TAG_HASH}"
    if [[ -z "$tag_hash" || "${tag_hash:0:n}" != "$SEED_TAG_HASH" ]]; then
        log_error "  tag ${SEED_TAG_NAME} resolves to '${tag_hash}', expected ${SEED_TAG_HASH}"
        ok=0
    else
        log "  tag ${SEED_TAG_NAME} -> ${tag_hash:0:12}... (unchanged)"
    fi

    if ! magpie_client "$READ_TOKEN" ls "test/" >/dev/null 2>&1; then
        log_error "  non-admin token no longer authenticates"
        ok=0
    else
        log "  non-admin token still authenticates"
    fi

    (( ok == 1 ))
}

# The successful upgrade: real `update` with no fault, crossing the
# 0.1.x-two-container -> 0.2.0-bundled boundary.
run_upgrade() {
    log "=== Scenario: upgrade (v0.1.6 -> v0.2.0+ bundled, no fault) ==="
    # preflight_host_clean() runs FIRST and unconditionally -- no
    # defensive pre-teardown before it. main()'s own sequencing already
    # guarantees the host is clean by the time this function is entered
    # (teardown_host() runs after every prior scenario, success or
    # failure -- see main()'s callers below); teardown-before-preflight
    # would instead mean this test could tear down a REAL, unrelated
    # magpie install on this host before ever getting a chance to detect
    # and refuse to run against it.
    preflight_host_clean

    install_v016 || return 1
    seed_data || return 1

    log "Running the real 'update' (this checkout's installer -- targets whatever version the repo's default branch resolves to)..."
    local update_out update_rc
    update_out="$(sudo bash "$NEW_SCRIPT" update --install-dir "$INSTALL_DIR" 2>&1)"
    update_rc=$?
    echo "$update_out" | tail -40

    if (( update_rc != 0 )); then
        record "upgrade: update command" "FAIL" "exited ${update_rc}: $(echo "$update_out" | tail -3 | tr '\n' ' ')"
        return 1
    fi
    record "upgrade: update command" "PASS" "exited 0"

    local services
    if ! services="$(compose_service_list)"; then
        record "upgrade: topology" "FAIL" "'docker compose ps --services' failed: ${services}"
        return 1
    fi
    if [[ "$services" != "magpie," ]]; then
        record "upgrade: topology" "FAIL" "expected single bundled 'magpie' service, got: ${services}"
        return 1
    fi
    record "upgrade: topology" "PASS" "single bundled container, no caddy sidecar"

    if ! wait_health; then
        record "upgrade: health" "FAIL" "not healthy after update"
        return 1
    fi
    record "upgrade: health" "PASS" "healthy on :${HTTP_PORT}"

    if assert_data_continuity; then
        record "upgrade: data continuity" "PASS" "artifacts byte-identical, tag intact, non-admin token intact"
    else
        record "upgrade: data continuity" "FAIL" "see log above"
        return 1
    fi

    local migrate_out version current
    migrate_out="$(compose_exec magpie magpie-ctl --format json migrate --check 2>&1)"
    version="$(echo "$migrate_out" | jq -r '.data.data_format_version // empty' 2>/dev/null)"
    current="$(echo "$migrate_out" | jq -r '.data.current_data_format_version // empty' 2>/dev/null)"
    if [[ -z "$version" || "$version" != "$current" ]]; then
        record "upgrade: migrate stamp" "FAIL" "data-format version=${version:-?} current=${current:-?}: ${migrate_out}"
        return 1
    fi
    record "upgrade: migrate stamp" "PASS" "data-format version ${version} == current"

    teardown_host
    return 0
}

# The fault-injection run: force the update's swap window to fail at the
# image-pull step (see this script's header comment for why DOCKER_CONFIG
# is safe/scoped) and confirm rollback_to_prior() restores a healthy prior
# (two-container) stack with data intact.
run_rollback() {
    log "=== Scenario: rollback (v0.1.6 -> v0.2.0+ bundled, forced image-pull failure) ==="
    # Same reasoning as run_upgrade(): preflight_host_clean() first, no
    # defensive pre-teardown ahead of it.
    preflight_host_clean

    install_v016 || return 1
    seed_data || return 1

    local fake_docker_config="${SCRATCH_DIR}/fake-docker-config"
    mkdir -p "$fake_docker_config"
    cat > "${fake_docker_config}/config.json" <<'EOF'
{
  "credHelpers": {
    "ghcr.io": "docker-credential-nonexistent-561b"
  }
}
EOF

    log "Running 'update' with a poisoned DOCKER_CONFIG (expect it to fail mid-swap and roll back)..."
    local update_out update_rc
    update_out="$(sudo env DOCKER_CONFIG="$fake_docker_config" bash "$NEW_SCRIPT" update --install-dir "$INSTALL_DIR" 2>&1)"
    update_rc=$?
    echo "$update_out" | tail -40

    if (( update_rc == 0 )); then
        record "rollback: update command" "FAIL" "expected non-zero exit (forced failure), got 0"
        return 1
    fi
    # The "ROLLED BACK" string match is best-effort/informational only,
    # not a pass/fail gate -- exact installer wording could change
    # without meaning rollback itself actually failed. The topology,
    # health, and data-continuity checks below are the real, structural
    # proof that rollback worked; this just adds color to the note.
    local rollback_note="failed as forced (exit ${update_rc})"
    if echo "$update_out" | grep -qi 'ROLLED BACK'; then
        rollback_note="${rollback_note}, and reported a clean rollback"
    else
        rollback_note="${rollback_note}; rollback confirmed structurally below (installer output didn't match the expected wording, not treated as a failure)"
    fi
    record "rollback: update command" "PASS" "$rollback_note"

    local services
    if ! services="$(compose_service_list)"; then
        record "rollback: topology" "FAIL" "'docker compose ps --services' failed: ${services}"
        return 1
    fi
    if [[ "$services" != "caddy,magpie," ]]; then
        record "rollback: topology" "FAIL" "expected the restored two-container topology (caddy,magpie), got: ${services}"
        return 1
    fi
    record "rollback: topology" "PASS" "restored two-container v0.1.6 topology"

    if ! wait_health; then
        record "rollback: health" "FAIL" "restored stack not healthy"
        return 1
    fi
    record "rollback: health" "PASS" "healthy on :${HTTP_PORT} after rollback"

    if assert_data_continuity; then
        record "rollback: data continuity" "PASS" "artifacts byte-identical, tag intact, non-admin token intact after rollback"
    else
        record "rollback: data continuity" "FAIL" "see log above"
        return 1
    fi

    teardown_host
    return 0
}

# =============================================================================
# Main
# =============================================================================

print_report() {
    echo ""
    echo "## Live Upgrade + Rollback Test Results (#561-B)"
    echo ""
    echo "| Feature | Status | Notes |"
    echo "|---------|--------|-------|"
    local row feature status notes
    for row in "${RESULTS[@]}"; do
        IFS='|' read -r feature status notes <<< "$row"
        echo "| ${feature} | ${status} | ${notes} |"
    done
    echo ""
}

usage() {
    cat >&2 <<EOF
Usage: MAGPIE_RUN_LIVE_UPGRADE_TEST=1 $0 [--only-upgrade|--only-rollback]

This is a real, root-requiring, host-mutating test (installs magpie
twice, for real, using systemd + Docker + the actual ghcr.io images). It
is not part of 'just e2e' / CI.
EOF
}

# Checked before anything else runs -- a missing tool or unusable sudo
# should fail fast and clearly, not surface as a confusing error partway
# through a real, root-mutating, multi-minute install.
require_prerequisites() {
    local missing=() cmd
    for cmd in docker git curl jq uv sudo systemctl sha256sum stat; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done
    if (( ${#missing[@]} > 0 )); then
        log_error "Missing required command(s): ${missing[*]}. Install them before running this test."
        exit 1
    fi
    if ! sudo -v; then
        log_error "'sudo -v' failed -- this script needs sudo for systemd/install/uninstall steps (see its header comment). Configure sudo access and try again."
        exit 1
    fi

    # Warms up the venv before any scenario runs. On a genuinely fresh
    # checkout (a new worktree, a CI runner, an operator's first run),
    # `uv run`'s FIRST invocation prints its own setup chatter ("Using
    # CPython...", "Creating virtual environment...", "Built magpie...",
    # "Installed N packages...") to stdout -- call sites that need
    # magpie_client()'s JSON output (e.g. seed_data()'s
    # `push_out="$(magpie_client ... 2>&1)"`) merge stderr into stdout so
    # real error output makes it into FAIL messages, which means that
    # chatter would otherwise land in front of the JSON response and
    # break `jq`'s parsing outright (jq requires the whole stream to be
    # valid JSON), producing a false "did not return a hash" FAIL even
    # though the push actually succeeded. A one-time, silent `uv sync`
    # here ensures every `uv run` call a scenario makes afterward is
    # already warm and prints nothing extra.
    if ! (cd "$REPO_ROOT" && uv sync) >/dev/null 2>&1; then
        log_error "'uv sync' failed in ${REPO_ROOT} -- cannot run the magpie client. Check the error by running it manually: (cd \"${REPO_ROOT}\" && uv sync)"
        exit 1
    fi
}

main() {
    if [[ "${MAGPIE_RUN_LIVE_UPGRADE_TEST:-}" != "1" ]]; then
        usage
        exit 0
    fi

    # Validated strictly -- a typoed flag (e.g. --only-upgrde) must not
    # silently fall through to running BOTH scenarios; that's twice the
    # runtime and twice the real, root-level install/teardown for a run
    # the operator only meant to run once.
    local run_upgrade_scenario=1
    local run_rollback_scenario=1
    case "${1:-}" in
        "") ;;
        --only-upgrade) run_rollback_scenario=0 ;;
        --only-rollback) run_upgrade_scenario=0 ;;
        -h|--help) usage; exit 0 ;;
        *)
            log_error "Unrecognized argument: '$1'"
            usage
            exit 1
            ;;
    esac
    if [[ -n "${2:-}" ]]; then
        log_error "Unexpected extra argument: '$2'"
        usage
        exit 1
    fi

    require_prerequisites

    local overall_ok=1

    if (( run_upgrade_scenario )); then
        if ! run_upgrade; then
            overall_ok=0
            teardown_host
        fi
    fi

    if (( run_rollback_scenario )); then
        if ! run_rollback; then
            overall_ok=0
            teardown_host
        fi
    fi

    print_report

    if (( overall_ok )); then
        log "PASS: real upgrade and real rollback both completed with data continuity."
        exit 0
    else
        log_error "FAIL: see the table above."
        exit 1
    fi
}

main "$@"
