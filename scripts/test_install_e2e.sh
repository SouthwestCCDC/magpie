#!/bin/bash
# Tier 2 of the installer CI (issues #354, #433): install magpie for real
# with scripts/magpie-deploy.sh, assert the RESULTING INSTALLATION behaves,
# then `uninstall --purge` and prove nothing is left behind.
#
# This is the tier that catches what the docker-compose E2E suite
# structurally cannot: systemd unit generation and activation, the GC timer
# and its ProtectHome=true sandbox, host-side ownership/permissions of the
# admin-token file (the class of bug issue #354 cites -- a /data owned by
# the wrong uid), and the auth gate as the installer actually wires it up.
#
# Destructive by design: it writes /opt/magpie, installs the global
# (non-namespaced) magpie.service / magpie-gc.{service,timer} units, and
# removes them again. It therefore REFUSES to run on a host that already
# has a magpie installation unless MAGPIE_E2E_FORCE=1 -- see preflight().
# Run it on a throwaway VM or a CI runner.
#
# ERREXIT-PRESERVING (deliberate, see issue #572): the pre-existing unit
# harnesses (e.g. scripts/test_installer_v020.sh) `set +e` after sourcing
# the installer, which hides exactly the failure mode this tier exists to
# catch -- a `set -euo pipefail` abort partway through an install. Nothing
# here disables errexit. Instead:
#   * the installer and every assertion run as separate processes,
#   * expected-failure and status-capturing calls use `if ! cmd` or
#     `status=0; cmd || status=$?`, which errexit explicitly permits,
#   * assertions record failures and keep going (so one run reports every
#     broken check), and the script exits non-zero if any recorded.
#
# Usage:
#   sudo scripts/test_install_e2e.sh
#   sudo MAGPIE_E2E_HTTP_PORT=8099 scripts/test_install_e2e.sh
#   sudo MAGPIE_E2E_KEEP_INSTALL=1 scripts/test_install_e2e.sh   # debug: skip purge
#
# Environment:
#   MAGPIE_E2E_HTTP_PORT     port the install publishes (default 8080)
#   MAGPIE_E2E_INSTALL_DIR   install dir (default /opt/magpie -- the
#                            documented default, so the default data dir
#                            /opt/magpie/data is what gets exercised)
#   MAGPIE_E2E_FORCE=1       proceed even if a magpie install already exists
#   MAGPIE_E2E_KEEP_INSTALL=1  leave the installation in place on exit

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

INSTALL_DIR="${MAGPIE_E2E_INSTALL_DIR:-/opt/magpie}"
DATA_DIR="${INSTALL_DIR}/data"
HTTP_PORT="${MAGPIE_E2E_HTTP_PORT:-8080}"
BASE_URL="http://127.0.0.1:${HTTP_PORT}"
ENV_FILE="${INSTALL_DIR}/etc/.env"
COMPOSE_FILE="${INSTALL_DIR}/docker-compose.yml"
DEPLOY_SCRIPT="${REPO_ROOT}/scripts/magpie-deploy.sh"

WORK_DIR=""
INSTALL_PERFORMED="false"
PURGE_VERIFIED="false"

# Credentials, populated by read_admin_token()/setup_tokens().
ADMIN_TOKEN=""
WRITE_TOKEN=""
READ_TOKEN=""

PASS_COUNT=0
declare -a FAILURES=()

# --- output helpers ---------------------------------------------------------

log() { echo "[installer-e2e] $*"; }
log_section() { echo ""; echo "[installer-e2e] === $* ==="; }

pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    echo "[installer-e2e] PASS: $*"
}

fail() {
    FAILURES+=("$1")
    echo "[installer-e2e] FAIL: $1" >&2
}

# Aborts the run. Used only for conditions that make every later assertion
# meaningless (no root, install itself failed), never for an assertion.
abort() {
    echo "[installer-e2e] ABORT: $*" >&2
    exit 1
}

# assert_eq <expected> <actual> <description>
assert_eq() {
    local expected="$1" actual="$2" description="$3"
    if [[ "$expected" == "$actual" ]]; then
        pass "${description} (${actual})"
    else
        fail "${description}: expected '${expected}', got '${actual}'"
    fi
}

# HTTP status code for a request, or "000" if the request never completed.
# curl's own failure is folded into the printed code rather than aborting,
# so a connection refusal shows up as a failed assertion with context.
http_status() {
    curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "$@" || echo "000"
}

# --- lifecycle -------------------------------------------------------------

preflight() {
    log_section "Preflight"

    if [[ $EUID -ne 0 ]]; then
        abort "must run as root (installs systemd units): sudo $0"
    fi
    for tool in docker systemctl curl git jq python3; do
        command -v "$tool" >/dev/null 2>&1 || abort "required tool not found: ${tool}"
    done
    docker info >/dev/null 2>&1 || abort "docker daemon is not running"
    # A container-only environment can't exercise the units this tier is
    # about, so say so plainly instead of failing obscurely later.
    systemctl is-system-running >/dev/null 2>&1 ||
        log "note: systemd reports $(systemctl is-system-running 2>&1 || true) -- continuing"

    # magpie.service / magpie-gc.* are GLOBAL unit names and INSTALL_DIR is
    # a fixed path: running here on a host that already has magpie would
    # clobber a real installation and then purge it. Refuse by default.
    if [[ "${MAGPIE_E2E_FORCE:-}" != "1" ]]; then
        # `if` blocks rather than `[[ ... ]] && ...`: a false `&&` chain is
        # itself a failing command, which errexit would abort on -- and
        # errexit stays on here (see the header).
        local existing=()
        if [[ -e "$INSTALL_DIR" ]]; then existing+=("${INSTALL_DIR} exists"); fi
        if [[ -e /etc/systemd/system/magpie.service ]]; then existing+=("magpie.service unit exists"); fi
        if [[ -e /etc/systemd/system/magpie-gc.timer ]]; then existing+=("magpie-gc.timer unit exists"); fi
        if (( ${#existing[@]} > 0 )); then
            abort "refusing to run: an existing magpie installation was detected (${existing[*]}). This test purges what it installs. Set MAGPIE_E2E_FORCE=1 to proceed anyway."
        fi
    fi

    WORK_DIR="$(mktemp -d)"
    log "install dir: ${INSTALL_DIR}  data dir: ${DATA_DIR}  url: ${BASE_URL}"
    log "source checkout: ${REPO_ROOT} (HEAD $(git -C "$REPO_ROOT" rev-parse --short HEAD))"
}

# Belt-and-braces teardown, always run. `uninstall --purge` is the tested
# path, but if the install aborted midway there may be units or containers
# with no INSTALL_DIR for uninstall to work from -- this leaves the host
# clean either way so a re-run (and any other CI job on this runner) starts
# from nothing.
cleanup() {
    local exit_code=$?
    set +u  # trap can fire before the globals below are assigned
    if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then
        rm -rf "$WORK_DIR"
    fi

    if [[ "${MAGPIE_E2E_KEEP_INSTALL:-}" == "1" ]]; then
        log "MAGPIE_E2E_KEEP_INSTALL=1 -- leaving the installation in place"
        exit "$exit_code"
    fi

    if [[ "$INSTALL_PERFORMED" == "true" && "$PURGE_VERIFIED" != "true" ]]; then
        log_section "Cleanup (uninstall did not complete -- forcing teardown)"
        systemctl stop magpie.service magpie-gc.timer magpie-gc.service 2>/dev/null || true
        systemctl disable magpie.service magpie-gc.timer 2>/dev/null || true
        if [[ -f "$COMPOSE_FILE" ]]; then
            docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" down --volumes --remove-orphans 2>/dev/null || true
        fi
        rm -f /etc/systemd/system/magpie.service \
              /etc/systemd/system/magpie-gc.service \
              /etc/systemd/system/magpie-gc.timer
        systemctl daemon-reload 2>/dev/null || true
        rm -rf "$INSTALL_DIR"
    fi
    exit "$exit_code"
}
trap cleanup EXIT

# Dumps everything needed to debug a failure from CI logs alone.
dump_diagnostics() {
    log_section "Diagnostics"
    systemctl status magpie.service --no-pager --full 2>&1 | head -40 || true
    journalctl -u magpie.service --no-pager --lines 100 2>&1 | tail -100 || true
    if [[ -f "$COMPOSE_FILE" ]]; then
        docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" ps 2>&1 || true
        docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" logs --tail 100 2>&1 || true
    fi
}

# --- the install ------------------------------------------------------------

run_install() {
    log_section "Install (from source, real systemd units)"

    # --source-dir installs THIS checkout's committed HEAD rather than a
    # fresh clone of the default branch, so a PR's changes to
    # Dockerfile.bundled / entrypoint.sh / docker/bundled/Caddyfile are
    # what actually gets tested.
    # --allow-unsupported-os: the installer supports Debian 12/13; the only
    # hosted runner with real systemd + Docker is Ubuntu. See the flag's
    # comment in magpie-deploy.sh.
    # --tls-mode off: no ACME/Let's Encrypt in CI (the flag is accepted for
    # compatibility and ignored -- the bundled image is HTTP-only).
    local status=0
    INSTALL_PERFORMED="true"
    "$DEPLOY_SCRIPT" install \
        --from-source \
        --source-dir "$REPO_ROOT" \
        --tls-mode off \
        --noninteractive \
        --install-dir "$INSTALL_DIR" \
        --http-port "$HTTP_PORT" \
        --allow-unsupported-os || status=$?

    assert_eq "0" "$status" "installer exit status"
    if (( status != 0 )); then
        dump_diagnostics
        abort "installer failed (exit ${status}); no point asserting on the result"
    fi
}

# --- assertions on the resulting installation -------------------------------

assert_units_active() {
    log_section "systemd units"
    assert_eq "active" "$(systemctl is-active magpie.service || true)" "magpie.service is-active"
    assert_eq "active" "$(systemctl is-active magpie-gc.timer || true)" "magpie-gc.timer is-active"
    assert_eq "enabled" "$(systemctl is-enabled magpie.service || true)" "magpie.service is-enabled"
    assert_eq "enabled" "$(systemctl is-enabled magpie-gc.timer || true)" "magpie-gc.timer is-enabled"
}

assert_container_healthy() {
    log_section "Container health"
    local container_id
    container_id="$(docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" ps -q magpie || true)"
    if [[ -z "$container_id" ]]; then
        fail "no running container for compose service 'magpie'"
        return 0
    fi
    # The compose healthcheck (docker/bundled/healthcheck.sh) is the same
    # signal the installer's own wait_for_healthy() uses; by this point it
    # should already read healthy.
    assert_eq "healthy" \
        "$(docker inspect -f '{{.State.Health.Status}}' "$container_id" || true)" \
        "container health status"
    assert_eq "running" \
        "$(docker inspect -f '{{.State.Status}}' "$container_id" || true)" \
        "container state"
}

# The bug class from issue #354: the admin token is a bearer credential
# written into the host data dir, so it must not be readable by anyone but
# root.
assert_admin_token_file() {
    log_section "Admin token file permissions"
    local token_file="${DATA_DIR}/admin-token"
    if [[ ! -f "$token_file" ]]; then
        fail "admin token file not found at ${token_file}"
        return 0
    fi
    assert_eq "root" "$(stat -c '%U' "$token_file")" "admin-token owner"
    assert_eq "600" "$(stat -c '%a' "$token_file")" "admin-token mode"
    # The data dir itself must not be world-readable either -- the artifact
    # tree and the token database live under it.
    local data_mode
    data_mode="$(stat -c '%a' "$DATA_DIR")"
    if [[ "${data_mode: -1}" == "0" ]]; then
        pass "data dir is not world-accessible (${data_mode})"
    else
        fail "data dir ${DATA_DIR} is world-accessible (mode ${data_mode})"
    fi
}

assert_health_endpoint() {
    log_section "Health through Caddy"
    assert_eq "200" "$(http_status "${BASE_URL}/health")" "GET /health"
}

# forward_auth is the auth gate: Caddy asks the app to validate the bearer
# token and only then copies the identity headers through. All three cases
# below go through the published port, i.e. through Caddy, not straight to
# uvicorn.
assert_forward_auth() {
    log_section "forward_auth"

    assert_eq "401" "$(http_status "${BASE_URL}/api/v1/artifacts")" \
        "unauthenticated GET /api/v1/artifacts"

    assert_eq "200" \
        "$(http_status -H "Authorization: Bearer ${ADMIN_TOKEN}" "${BASE_URL}/api/v1/artifacts")" \
        "authenticated GET /api/v1/artifacts"

    assert_eq "401" \
        "$(http_status -H "Authorization: Bearer definitely-not-a-valid-token" "${BASE_URL}/api/v1/artifacts")" \
        "invalid-token GET /api/v1/artifacts"
}

# Issue #525: the identity headers forward_auth sets are trusted by the
# app, so Caddy must strip any client-supplied copy BEFORE validating.
# Without the strip, either of these requests would be honored as the
# identity the client claimed.
assert_forged_headers_rejected() {
    log_section "Forged X-Magpie-* headers"

    assert_eq "401" \
        "$(http_status \
            -H "X-Magpie-User: attacker" \
            -H "X-Magpie-Scope: admin" \
            "${BASE_URL}/api/v1/artifacts")" \
        "forged identity headers, no token: GET /api/v1/artifacts"

    assert_eq "401" \
        "$(http_status \
            -H "X-Magpie-User: attacker" \
            -H "X-Magpie-Scope: admin" \
            "${BASE_URL}/api/v1/tokens")" \
        "forged identity headers, no token: GET /api/v1/tokens (admin endpoint)"

    # A real but read-scoped token plus a forged admin scope must not reach
    # an admin-only endpoint: the token is valid, so forward_auth passes,
    # and the ONLY thing preventing escalation is that Caddy replaced the
    # client's X-Magpie-Scope with the validated one.
    assert_eq "403" \
        "$(http_status \
            -H "Authorization: Bearer ${READ_TOKEN}" \
            -H "X-Magpie-Scope: admin" \
            -H "X-Magpie-User: attacker" \
            "${BASE_URL}/api/v1/tokens")" \
        "read token + forged admin scope: GET /api/v1/tokens"

    assert_eq "403" \
        "$(http_status -X POST \
            -H "Authorization: Bearer ${READ_TOKEN}" \
            -H "X-Magpie-Scope: write" \
            -F "file=@${WORK_DIR}/artifact.bin" \
            "${BASE_URL}/api/v1/upload/installer-e2e/forged-scope")" \
        "read token + forged write scope: POST /api/v1/upload"
}

# Round-trip through the installed stack: upload via the API, download via
# the URL the API hands back (served by Caddy's file_server out of the host
# data dir), and require byte equality.
assert_round_trip() {
    log_section "Push/get round trip"

    local artifact="${WORK_DIR}/artifact.bin"
    local downloaded="${WORK_DIR}/downloaded.bin"
    local upload_json="${WORK_DIR}/upload.json"

    local status=0
    curl -sS --max-time 60 \
        -H "Authorization: Bearer ${WRITE_TOKEN}" \
        -F "file=@${artifact}" \
        -o "$upload_json" \
        "${BASE_URL}/api/v1/upload/installer-e2e/round-trip" || status=$?
    if (( status != 0 )); then
        fail "upload request failed (curl exit ${status})"
        return 0
    fi

    local uploaded_hash download_url
    uploaded_hash="$(jq -r '.hash // empty' "$upload_json")"
    download_url="$(jq -r '.download_url // empty' "$upload_json")"
    if [[ -z "$download_url" ]]; then
        fail "upload response had no download_url: $(head -c 400 "$upload_json")"
        return 0
    fi
    assert_eq "$(sha256sum "$artifact" | cut -d' ' -f1)" "$uploaded_hash" \
        "server-reported upload hash"

    status=0
    curl -sS --max-time 60 -f \
        -H "Authorization: Bearer ${WRITE_TOKEN}" \
        -o "$downloaded" \
        "${BASE_URL}${download_url}" || status=$?
    if (( status != 0 )); then
        fail "download of ${download_url} failed (curl exit ${status})"
        return 0
    fi

    if cmp -s "$artifact" "$downloaded"; then
        pass "downloaded artifact is byte-identical ($(stat -c '%s' "$artifact") bytes)"
    else
        fail "downloaded artifact differs from the uploaded one"
    fi
}

# The GC timer's oneshot service runs under a strict sandbox
# (ProtectSystem=strict, ProtectHome=true) and through `docker compose run`,
# which is why it is worth triggering for real rather than trusting that
# the timer is merely active. `systemctl start` on a Type=oneshot unit
# blocks until it finishes and reports its exit status.
assert_gc_service_runs() {
    log_section "GC service"
    local status=0
    systemctl start magpie-gc.service || status=$?
    assert_eq "0" "$status" "systemctl start magpie-gc.service"
    if (( status != 0 )); then
        journalctl -u magpie-gc.service --no-pager --lines 50 2>&1 | tail -50 || true
        return 0
    fi
    # A oneshot that ran and exited 0 lands in "inactive"; anything else
    # (notably "failed") means the sandboxed command itself broke.
    local result
    result="$(systemctl show -p Result --value magpie-gc.service || true)"
    assert_eq "success" "$result" "magpie-gc.service result"
}

# --- credentials for the assertions above -----------------------------------

read_admin_token() {
    log_section "Credentials"
    local token_file="${DATA_DIR}/admin-token"
    [[ -f "$token_file" ]] || abort "no admin token at ${token_file}; cannot exercise the auth gate"
    ADMIN_TOKEN="$(tr -d '[:space:]' < "$token_file")"
    [[ -n "$ADMIN_TOKEN" ]] || abort "admin token file is empty"
    log "admin token loaded (${#ADMIN_TOKEN} chars)"
}

# Creates a token with magpie-ctl inside the running container -- the same
# path an operator uses (`docker compose exec magpie magpie-ctl ...`), so
# this also covers that the ctl entrypoint works in the bundled image.
create_token() {
    local name="$1" scope="$2"
    local json
    json="$(docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
        exec -T magpie magpie-ctl --format json token create --name "$name" --scope "$scope")"
    jq -r '.data.token // empty' <<< "$json"
}

setup_tokens() {
    WRITE_TOKEN="$(create_token "installer-e2e-write" "write")"
    [[ -n "$WRITE_TOKEN" ]] || abort "failed to create a write-scoped token"
    READ_TOKEN="$(create_token "installer-e2e-read" "read")"
    [[ -n "$READ_TOKEN" ]] || abort "failed to create a read-scoped token"
    log "created write- and read-scoped tokens"

    # 1 MiB of random bytes: big enough that a truncating or re-encoding bug
    # in the upload/download path shows up in the byte comparison.
    head -c 1048576 /dev/urandom > "${WORK_DIR}/artifact.bin"
}

# --- uninstall --------------------------------------------------------------

assert_purge_leaves_nothing() {
    log_section "uninstall --purge"

    local status=0
    # --yes: --purge asks for confirmation, and --noninteractive alone
    # hard-fails on a prompt rather than answering it.
    "$DEPLOY_SCRIPT" uninstall --purge --yes --noninteractive --install-dir "$INSTALL_DIR" || status=$?
    assert_eq "0" "$status" "uninstall --purge exit status"
    if (( status != 0 )); then
        return 0
    fi

    local leftovers=()
    if [[ -e "$INSTALL_DIR" ]]; then leftovers+=("install dir ${INSTALL_DIR}"); fi
    if [[ -e "$DATA_DIR" ]]; then leftovers+=("data dir ${DATA_DIR}"); fi
    local unit
    for unit in magpie.service magpie-gc.service magpie-gc.timer; do
        if [[ -e "/etc/systemd/system/${unit}" ]]; then leftovers+=("unit file ${unit}"); fi
    done
    # systemd's own view, not just the filesystem: a stale enablement
    # symlink under a *.target.wants directory would still show up here.
    local known_units
    known_units="$(systemctl list-unit-files 'magpie*' --no-legend --no-pager 2>/dev/null || true)"
    if [[ -n "$known_units" ]]; then leftovers+=("systemd still knows units: ${known_units//$'\n'/; }"); fi

    local containers
    containers="$(docker ps -a --filter 'name=magpie' --format '{{.Names}} ({{.Status}})' || true)"
    if [[ -n "$containers" ]]; then leftovers+=("containers: ${containers//$'\n'/; }"); fi
    local volumes
    volumes="$(docker volume ls --filter 'name=magpie' --format '{{.Name}}' || true)"
    if [[ -n "$volumes" ]]; then leftovers+=("volumes: ${volumes//$'\n'/; }"); fi

    if (( ${#leftovers[@]} == 0 )); then
        PURGE_VERIFIED="true"
        pass "purge left nothing behind (install dir, data dir, units, containers, volumes)"
    else
        for leftover in "${leftovers[@]}"; do
            fail "purge leftover: ${leftover}"
        done
    fi
}

# --- main -------------------------------------------------------------------

main() {
    preflight
    run_install

    assert_units_active
    assert_container_healthy
    assert_admin_token_file
    assert_health_endpoint

    read_admin_token
    setup_tokens

    assert_forward_auth
    assert_forged_headers_rejected
    assert_round_trip
    assert_gc_service_runs

    assert_purge_leaves_nothing

    log_section "Summary"
    log "${PASS_COUNT} assertion(s) passed, ${#FAILURES[@]} failed"
    if (( ${#FAILURES[@]} > 0 )); then
        for failure in "${FAILURES[@]}"; do
            echo "  - ${failure}" >&2
        done
        dump_diagnostics
        exit 1
    fi
    log "installer E2E passed"
}

main "$@"
