#!/bin/bash
# Test script for issue #603 -- the update safety envelope's A2/A4
# (artifact byte-identity/tag continuity) probe silently self-skipping on
# a two-container -> bundled (2->1) upgrade, because capture_probe_state()
# hardcoded the bundled image's same-container Caddy port (127.0.0.1:8080)
# unconditionally. In the pre-0.2.0 two-container topology, the `magpie`
# container runs bare uvicorn with no Caddy of its own -- a hit at :8080
# from inside it finds nothing, so the probe can never capture a "before"
# artifact hash/tag, and assert_post_update() treats A2/A4 as vacuously
# satisfied on exactly the crossover the envelope exists to protect.
#
# Same pattern as scripts/test_installer_issue561.sh: source the real
# magpie-deploy.sh functions, shadow `docker`/`compose_exec` as needed, no
# Docker/systemd/root required.

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

# Sets up a minimal fake install: install_dir (with etc/.env +
# docker-compose.yml) and data_dir (with magpie.db). Echoes
# "install_dir data_dir" so callers can capture both with `read`.
setup_fake_install() {
    local name="$1"
    local install_dir="${TEST_DIR}/${name}"
    local data_dir="${install_dir}/data"
    mkdir -p "${install_dir}/etc" "${data_dir}/artifacts"

    cat > "${install_dir}/etc/.env" << EOF
MAGPIE_DATA_DIR=${data_dir}
MAGPIE_HTTP_PORT=8080
MAGPIE_ADMIN_TOKEN_SINK=file
MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH=/data/admin-token
EOF

    cat > "${install_dir}/docker-compose.yml" << 'EOF'
services:
  magpie:
    image: ghcr.io/southwestccdc/magpie:0.1.6-bundled
EOF

    printf '%s' "OLD_DB_CONTENT" > "${data_dir}/magpie.db"
    echo "${install_dir} ${data_dir}"
}

# ---------------------------------------------------------------------------
# probe_magpie_server_url()
# ---------------------------------------------------------------------------
test_probe_server_url_detects_running_caddy_service() {
    log "Test 1: probe_magpie_server_url() returns caddy:80 when a separate caddy service is running, 127.0.0.1:8080 otherwise"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "probe_url_caddy_present")

    local url
    url=$(
        (
            docker() {
                if [[ "$1" == "compose" ]]; then
                    for arg in "$@"; do
                        if [[ "$arg" == "ps" ]]; then
                            printf 'caddy\nmagpie\n'
                            return 0
                        fi
                    done
                fi
                return 1
            }
            INSTALL_DIR="$install_dir" probe_magpie_server_url
        )
    )
    [[ "$url" == "http://caddy:80" ]] || fail "expected http://caddy:80 when a caddy service is running, got: $url"
    log "  ✓ a running caddy service -> http://caddy:80 (two-container topology)"

    url=$(
        (
            docker() {
                if [[ "$1" == "compose" ]]; then
                    for arg in "$@"; do
                        [[ "$arg" == "ps" ]] && { printf 'magpie\n'; return 0; }
                    done
                fi
                return 1
            }
            INSTALL_DIR="$install_dir" probe_magpie_server_url
        )
    )
    [[ "$url" == "http://127.0.0.1:8080" ]] || fail "expected http://127.0.0.1:8080 when no caddy service is running, got: $url"
    log "  ✓ no caddy service -> http://127.0.0.1:8080 (bundled, same-container Caddy)"

    url=$(
        (
            docker() { return 1; }  # docker compose ps fails entirely (e.g. project not up)
            INSTALL_DIR="$install_dir" probe_magpie_server_url
        )
    )
    [[ "$url" == "http://127.0.0.1:8080" ]] || fail "expected the safe bundled-topology default when 'docker compose ps' fails outright, got: $url"
    log "  ✓ 'docker compose ps' failing outright still falls back to the bundled default, not an empty/broken URL"
}

# ---------------------------------------------------------------------------
# capture_probe_state() -- the must-have regression test: A2/A4 must
# ACTUALLY RUN (capture a real before-hash), not vacuously skip, when the
# probe is sourced from a two-container-style install. The compose_exec
# shadow below ONLY returns valid magpie CLI output for calls carrying
# `MAGPIE_SERVER=http://caddy:80` -- any call still using the OLD
# hardcoded bundled port (127.0.0.1:8080) fails, exactly reproducing the
# real two-container behavior (a hit at :8080 from inside the `magpie`
# container, which has no Caddy of its own, finds nothing). This is what
# actually proves the fix is wired in, not just present as dead code: if
# capture_probe_state() still hardcoded 127.0.0.1:8080, PROBE_ARTIFACT_PATH
# would stay empty and this test would fail exactly the way issue #603
# describes.
# ---------------------------------------------------------------------------
test_capture_probe_state_reaches_two_container_topology_and_captures_artifact() {
    log "Test 2: capture_probe_state() reaches a two-container-style prior install and captures a real before-hash (issue #603 regression)"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "probe_two_container")

    local out
    out=$(
        (
            curl() { return 0; }  # health_check_url() reachability gate
            docker() {
                if [[ "$1" == "compose" ]]; then
                    for arg in "$@"; do
                        [[ "$arg" == "ps" ]] && { printf 'caddy\nmagpie\n'; return 0; }
                    done
                fi
                return 1
            }
            compose_exec() {
                case "$*" in
                    *"token create"*) echo "mgp_ADMIN_faketoken123" ;;
                    *"token list"*) echo "Total: 1 token(s)" ;;
                    *"migrate --check"*) echo "Data-format version: 0 (current: 1)" ;;
                    *"MAGPIE_SERVER=http://caddy:80"*"ls -r"*) echo "myproj/myart" ;;
                    *"MAGPIE_SERVER=http://caddy:80"*"info myproj/myart:v1"*)
                        printf 'Hash:        deadbeef123\nTags:        v1, latest\n'
                        ;;
                    *"MAGPIE_SERVER=http://caddy:80"*"info myproj/myart"*)
                        printf 'Hash:        deadbeef123\nTags:        v1, latest\n'
                        ;;
                    # Any data-plane call still using the OLD hardcoded
                    # bundled port instead of the two-container-aware
                    # caddy:80 must fail -- reproduces the real bug
                    # (nothing listens at :8080 inside the pre-0.2.0
                    # `magpie` container).
                    *"MAGPIE_SERVER=http://127.0.0.1:8080"*"ls"*) return 1 ;;
                    *"MAGPIE_SERVER=http://127.0.0.1:8080"*"info"*) return 1 ;;
                    *) return 1 ;;
                esac
            }
            INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" HTTP_PORT=1 BIND_IP="" \
            capture_probe_state
            echo "PROBE_ARTIFACT_PATH=${PROBE_ARTIFACT_PATH}"
            echo "PROBE_ARTIFACT_REF=${PROBE_ARTIFACT_REF}"
            echo "PROBE_ARTIFACT_SHA256=${PROBE_ARTIFACT_SHA256}"
        ) 2>&1
    )

    echo "$out" | grep -qF "PROBE_ARTIFACT_PATH=myproj/myart" \
        || fail "capture_probe_state() did not capture a probe artifact path against a two-container-style install -- A2/A4 would silently vacuously skip, exactly issue #603's bug. Output: $out"
    echo "$out" | grep -qF "PROBE_ARTIFACT_REF=v1" \
        || fail "capture_probe_state() did not capture the probe artifact's tag: $out"
    echo "$out" | grep -qF "PROBE_ARTIFACT_SHA256=deadbeef123" \
        || fail "capture_probe_state() did not capture a real before-hash against a two-container-style install: $out"
    log "  ✓ capture_probe_state() reaches the two-container topology's Caddy and captures a real before-hash"
}

# ---------------------------------------------------------------------------
# End-to-end: capture_probe_state() (pre-swap, two-container-aware) feeds
# assert_post_update() (post-swap, always bundled -- unaffected by this
# fix, hardcoded 127.0.0.1:8080 stays correct there) -- confirms A2/A4
# GENUINELY COMPARE, not vacuously pass, on a simulated 2->1 crossover.
# ---------------------------------------------------------------------------
test_assert_post_update_genuinely_compares_on_two_container_crossover() {
    log "Test 3: assert_post_update() genuinely runs A2/A4 (not vacuous) when fed a probe captured from a two-container-style install"

    local install_dir data_dir
    read -r install_dir data_dir < <(setup_fake_install "probe_two_container_e2e")

    local out
    out=$(
        (
            curl() { return 0; }
            docker() {
                if [[ "$1" == "compose" ]]; then
                    for arg in "$@"; do
                        [[ "$arg" == "ps" ]] && { printf 'caddy\nmagpie\n'; return 0; }
                    done
                fi
                return 1
            }
            # Pre-swap (capture) shadow: only the two-container-aware
            # caddy:80 URL works, matching the real old topology.
            compose_exec() {
                case "$*" in
                    *"token create"*) echo "mgp_ADMIN_faketoken123" ;;
                    *"token list"*) echo "Total: 1 token(s)" ;;
                    *"migrate --check"*) echo "Data-format version: 0 (current: 1)" ;;
                    *"MAGPIE_SERVER=http://caddy:80"*"ls -r"*) echo "myproj/myart" ;;
                    *"MAGPIE_SERVER=http://caddy:80 "*"info myproj/myart:v1"*|*"MAGPIE_SERVER=http://caddy:80"*"info myproj/myart:v1"*)
                        printf 'Hash:        deadbeef123\nTags:        v1, latest\n'
                        ;;
                    *"MAGPIE_SERVER=http://caddy:80"*"info myproj/myart"*)
                        printf 'Hash:        deadbeef123\nTags:        v1, latest\n'
                        ;;
                    *) return 1 ;;
                esac
            }
            INSTALL_DIR="$install_dir" DATA_DIR="$data_dir" HTTP_PORT=1 BIND_IP="" \
            capture_probe_state

            # Post-swap: the container is now unconditionally bundled --
            # assert_post_update()'s own hardcoded 127.0.0.1:8080 is
            # correct here and deliberately unchanged by this fix. Reports
            # the SAME hash the pre-swap probe captured, proving the
            # comparison is genuinely happening (a still-vacuous A2/A4
            # would pass regardless of what this shadow returns).
            wait_for_healthy() { return 0; }
            compose_exec() {
                case "$*" in
                    *"token list"*) echo "Total: 1 token(s)" ;;
                    *"migrate --check"*) echo "Data-format version: 1 (current: 1)" ;;
                    *"MAGPIE_SERVER=http://127.0.0.1:8080"*"ls"*) return 0 ;;
                    *"MAGPIE_SERVER=http://127.0.0.1:8080"*"info myproj/myart:v1"*)
                        printf 'Hash:        deadbeef123\nTags:        v1, latest\n'
                        ;;
                    *"MAGPIE_SERVER=http://127.0.0.1:8080"*"get myproj/myart:v1"*) echo "Downloaded: /tmp/magpie-update-probe" ;;
                    *"token revoke"*) return 0 ;;
                    *"rm -f /tmp/magpie-update-probe"*) return 0 ;;
                    *) return 1 ;;
                esac
            }
            PRIOR_TOKEN_ROW_COUNT="1" \
            assert_post_update
            echo "ASSERT_RC=$?"
            printf 'FAILURE: %s\n' "${ASSERT_FAILURES[@]}"
        ) 2>&1
    )

    echo "$out" | grep -qF "ASSERT_RC=0" \
        || fail "assert_post_update() did not pass against a matching post-update hash on a two-container-sourced crossover: $out"
    echo "$out" | grep -qi "^FAILURE: A2\|^FAILURE: A4" \
        && fail "assert_post_update() reported an A2/A4 failure despite matching hashes -- something in the wiring is broken: $out"
    log "  ✓ assert_post_update() genuinely compares (not vacuously skips) A2/A4 fed from a two-container-sourced probe"
}

log "Running tests for the topology-aware update-assert probe (issue #603)"
log ""

test_probe_server_url_detects_running_caddy_service
test_capture_probe_state_reaches_two_container_topology_and_captures_artifact
test_assert_post_update_genuinely_compares_on_two_container_crossover

log ""
log "All tests passed!"
