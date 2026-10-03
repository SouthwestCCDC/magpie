#!/bin/bash
# Checks a release's pushed image and rendered assets before the release
# workflow moves `latest` or publishes the GitHub Release (#571). Each
# subcommand is one check; release.yml runs them in order. Runnable locally
# against an already-published release (needs Docker, root via sudo for the
# installer checks, and network access to GitHub and ghcr.io).
#
# Usage: scripts/release_verify.sh <subcommand>
#   assets      SHA256SUMS verify; compose/env pin IMAGE_REPO:VERSION@DIGEST;
#               the registry's VERSION tag is that digest
#   smoke       restricted start of the image (non-root, read-only root fs,
#               no capabilities); /health. Also run on arm64
#   compose     plain compose from DIST on a fresh data dir
#   compose-upgrade   compose from PREV_DIST, then DIST's compose file over it
#   installer   DIST's installer: install --release, checks, uninstall --purge
#   installer-upgrade PREV_DIST's installer installs PREV_VERSION, DIST's
#               installer updates to VERSION, then uninstall --purge
#   previous    print the newest published release older than VERSION that
#               has compose/env/installer assets (empty if none)
#
# Env: VERSION (e.g. 0.2.0-rc9), DIGEST (sha256:...), IMAGE_REPO
#      (default ghcr.io/southwestccdc/magpie), DIST, PREV_DIST, PREV_VERSION,
#      GITHUB_REPOSITORY and GH_TOKEN (previous), HTTP_PORT (default 8080)
set -euo pipefail

IMAGE_REPO="${IMAGE_REPO:-ghcr.io/southwestccdc/magpie}"
HTTP_PORT="${HTTP_PORT:-8080}"
BASE_URL="http://127.0.0.1:${HTTP_PORT}"
PROJECT="magpie-release-verify"

log() { printf '[release-verify] %s\n' "$*"; }
die() {
    printf '::error::release-verify: %s\n' "$*" >&2
    exit 1
}

need() { [[ -n "${!1:-}" ]] || die "$1 is not set"; }

# /health reports the package version in PEP 440 form (0.2.0rc9 for 0.2.0-rc9).
wait_healthy() {
    local want="$1" body="" got pep440
    for _ in $(seq 1 90); do
        body="$(curl -fsS "${BASE_URL}/health" 2>/dev/null)" && break
        body=""
        sleep 2
    done
    [[ -n "$body" ]] || die "${BASE_URL}/health never answered"
    got="$(jq -r .version <<<"$body")"
    pep440="$(sed -E 's/-(a|b|rc)\.?([0-9]+)$/\1\2/' <<<"$want")"
    [[ "$got" == "$want" || "$got" == "$pep440" ]] ||
        die "/health reports version ${got}, expected ${want}"
    log "healthy at ${want}"
}

http_status() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

# Every process in the container runs as the given uid.
assert_uid() {
    local cid="$1" want="$2" uids
    uids="$(docker top "$cid" -eo pid,uid | awk 'NR > 1 {print $2}' | sort -u | paste -sd' ')"
    [[ "$uids" == "$want" ]] || die "container processes run as uid(s) '${uids}', expected ${want}"
    log "every process runs as uid ${want}"
}

# Anonymous requests are refused; the admin token authenticates.
assert_auth() {
    local token="$1" code
    code="$(http_status "${BASE_URL}/api/v1/tokens")"
    [[ "$code" == 401 ]] || die "anonymous /api/v1/tokens returned ${code}, expected 401"
    code="$(http_status -H "Authorization: Bearer ${token}" "${BASE_URL}/api/v1/auth/validate")"
    [[ "$code" == 200 ]] || die "admin token rejected by /api/v1/auth/validate (${code})"
    log "anonymous request refused, admin token accepted"
}

push_artifact() {
    local token="$1" path="$2" file="$3" code
    code="$(http_status -X POST -H "Authorization: Bearer ${token}" \
        -F "file=@${file};type=application/octet-stream" \
        "${BASE_URL}/api/v1/upload/${path}")"
    [[ "$code" == 200 || "$code" == 201 ]] || die "upload of ${path} returned ${code}"
}

assert_artifact() {
    local token="$1" path="$2" file="$3" got
    got="$(mktemp)"
    curl -fsS -H "Authorization: Bearer ${token}" -o "$got" "${BASE_URL}/artifacts/${path}/latest" ||
        die "download of ${path} failed"
    cmp -s "$got" "$file" || die "${path} came back different from what was uploaded"
    rm -f "$got"
    log "artifact ${path} round-trips intact"
}

# One fixed path per check, overwritten on each run, so repeated local runs
# don't pile up files; each check removes its own when it passes.
random_file() {
    local f="${TMPDIR:-/tmp}/${PROJECT}-$1.bin"
    head -c 1048576 /dev/urandom >"$f"
    echo "$f"
}

cmd_assets() {
    need VERSION
    need DIGEST
    need DIST
    local pinned="${IMAGE_REPO}:${VERSION}@${DIGEST}" registry
    (cd "$DIST" && sha256sum -c SHA256SUMS) || die "SHA256SUMS does not match the rendered files"
    grep -qF "\${MAGPIE_IMAGE:-${pinned}}" "${DIST}/docker-compose.yml" ||
        die "docker-compose.yml does not pin ${pinned}"
    grep -qF "# MAGPIE_IMAGE=${pinned}" "${DIST}/env.example" || die "env.example does not name ${pinned}"
    grep -qF "HARDCODED_VERSION=\"${VERSION}\"" "${DIST}/magpie-deploy.sh" ||
        die "magpie-deploy.sh is not stamped ${VERSION}"
    for tag in "$VERSION" "${VERSION}-bundled"; do
        registry="$(docker buildx imagetools inspect "${IMAGE_REPO}:${tag}" --format '{{json .Manifest.Digest}}' | tr -d '"')"
        [[ "$registry" == "$DIGEST" ]] || die "${IMAGE_REPO}:${tag} is ${registry}, expected ${DIGEST}"
    done
    log "assets verified and pinned to ${pinned}"
}

cmd_smoke() {
    need VERSION
    need DIGEST
    local ref="${IMAGE_REPO}:${VERSION}@${DIGEST}" data cid
    data="$(mktemp -d)"
    sudo chown 10001:10001 "$data"
    cid="$(docker run -d --rm --name "${PROJECT}-smoke" \
        --user 10001:10001 --read-only --cap-drop ALL \
        --tmpfs /tmp --tmpfs /run \
        -e MAGPIE_STORAGE_PATH=/data/artifacts -e MAGPIE_DATABASE_PATH=/data/magpie.db \
        -e MAGPIE_ADMIN_TOKEN_SINK=discard \
        -v "${data}:/data" -p "127.0.0.1:${HTTP_PORT}:8080" "$ref")"
    trap '(($? == 0)) || docker logs "'"$cid"'" 2>&1 | tail -n 50; docker rm -f "'"$cid"'" >/dev/null 2>&1; sudo rm -rf "'"$data"'"' EXIT
    wait_healthy "$VERSION"
    assert_uid "$cid" 10001
    log "restricted start healthy on $(docker exec "$cid" uname -m)"
}

# Starts compose from <dir> (docker-compose.yml + .env) and waits for VERSION.
compose_up() {
    local dir="$1" want="$2"
    docker compose -p "$PROJECT" --project-directory "$dir" up -d --quiet-pull
    wait_healthy "$want"
}

compose_workdir() {
    local from="$1" dir
    dir="$(mktemp -d)"
    cp "${from}/docker-compose.yml" "${dir}/docker-compose.yml"
    cp "${from}/env.example" "${dir}/.env"
    printf 'MAGPIE_ADMIN_TOKEN_SINK=file\nMAGPIE_HTTP_PORT=%s\nMAGPIE_BIND_IP=127.0.0.1\n' "$HTTP_PORT" >>"${dir}/.env"
    echo "$dir"
}

# Container logs only when the check failed (rc != 0).
compose_cleanup() {
    local dir="$1" rc="$2"
    ((rc == 0)) || docker compose -p "$PROJECT" --project-directory "$dir" logs --tail 50 || true
    docker compose -p "$PROJECT" --project-directory "$dir" down -v || true
    sudo rm -rf "$dir"
}

cmd_compose() {
    need VERSION
    need DIST
    local dir token cid blob
    dir="$(compose_workdir "$DIST")"
    trap 'compose_cleanup "'"$dir"'" $?' EXIT
    compose_up "$dir" "$VERSION"
    cid="$(docker compose -p "$PROJECT" --project-directory "$dir" ps -q magpie)"
    assert_uid "$cid" 10001
    token="$(sudo cat "${dir}/data/admin-token")"
    assert_auth "$token"
    sudo ls "${dir}/data/backups" | grep -qE '\.pre-v[0-9]+$' || die "no pre-migration copy in data/backups"
    blob="$(random_file compose)"
    push_artifact "$token" release-verify/compose "$blob"
    assert_artifact "$token" release-verify/compose "$blob"
    rm -f "$blob"
}

cmd_compose_upgrade() {
    need VERSION
    need DIST
    need PREV_DIST
    need PREV_VERSION
    local dir token blob
    dir="$(compose_workdir "$PREV_DIST")"
    trap 'compose_cleanup "'"$dir"'" $?' EXIT
    compose_up "$dir" "$PREV_VERSION"
    token="$(sudo cat "${dir}/data/admin-token")"
    blob="$(random_file upgrade)"
    push_artifact "$token" release-verify/upgrade "$blob"
    # What an operator does: replace the compose file, keep .env and data.
    cp "${DIST}/docker-compose.yml" "${dir}/docker-compose.yml"
    compose_up "$dir" "$VERSION"
    assert_auth "$token"
    assert_artifact "$token" release-verify/upgrade "$blob"
    rm -f "$blob"
    log "plain compose upgraded ${PREV_VERSION} -> ${VERSION}"
}

INSTALL_DATA=/opt/magpie/data

# Best effort, from the EXIT trap, after a failed check.
installer_cleanup() {
    local deploy="$1" rc="$2"
    ((rc == 0)) || sudo journalctl -u magpie.service --no-pager -n 50 || true
    sudo bash "$deploy" uninstall --purge --yes || true
}

# On success: the purge is part of the check, so it must work.
installer_purge() {
    local deploy="$1"
    trap - EXIT
    sudo bash "$deploy" uninstall --purge --yes || die "uninstall --purge failed"
    [[ ! -e /opt/magpie ]] || die "/opt/magpie left behind by uninstall --purge"
    ! id magpie >/dev/null 2>&1 || die "magpie account left behind by uninstall --purge"
    ! sudo docker ps -a --format '{{.Names}}' | grep -q magpie ||
        die "magpie containers left behind by uninstall --purge"
}

installer_install() {
    local deploy="$1" release="$2"
    sudo bash "$deploy" install --release "v${release}" --noninteractive --tls-mode off \
        --http-port "$HTTP_PORT" --allow-unsupported-os
    wait_healthy "$release"
}

installer_container() {
    sudo docker ps --filter "publish=${HTTP_PORT}" --format '{{.ID}}' | head -n1
}

cmd_installer() {
    need VERSION
    need DIST
    local deploy="${DIST}/magpie-deploy.sh" token blob cid
    trap 'installer_cleanup "'"$deploy"'" $?' EXIT
    installer_install "$deploy" "$VERSION"
    systemctl is-active --quiet magpie.service || die "magpie.service is not active"
    systemctl is-active --quiet magpie-gc.timer || die "magpie-gc.timer is not active"
    cid="$(installer_container)"
    assert_uid "$cid" "$(id -u magpie)"
    token="$(sudo cat "${INSTALL_DATA}/admin-token")"
    assert_auth "$token"
    blob="$(random_file installer)"
    push_artifact "$token" release-verify/installer "$blob"
    assert_artifact "$token" release-verify/installer "$blob"
    sudo systemctl start magpie-gc.service || die "magpie-gc.service failed"
    log "gc run succeeded"
    installer_purge "$deploy"
    rm -f "$blob"
    log "installer lifecycle passed"
}

cmd_installer_upgrade() {
    need VERSION
    need DIST
    need PREV_DIST
    need PREV_VERSION
    local deploy="${DIST}/magpie-deploy.sh" token blob
    trap 'installer_cleanup "'"$deploy"'" $?' EXIT
    installer_install "${PREV_DIST}/magpie-deploy.sh" "$PREV_VERSION"
    token="$(sudo cat "${INSTALL_DATA}/admin-token")"
    blob="$(random_file installer-upgrade)"
    push_artifact "$token" release-verify/installer-upgrade "$blob"
    sudo bash "$deploy" update --release "v${VERSION}" --noninteractive
    wait_healthy "$VERSION"
    assert_auth "$token"
    assert_artifact "$token" release-verify/installer-upgrade "$blob"
    installer_purge "$deploy"
    rm -f "$blob"
    log "installer upgraded ${PREV_VERSION} -> ${VERSION}"
}

cmd_previous() {
    need VERSION
    need GITHUB_REPOSITORY
    gh api --paginate "repos/${GITHUB_REPOSITORY}/releases?per_page=100" --jq '
        .[] | select(.draft | not)
        | select([.assets[].name | select(. == "docker-compose.yml" or . == "env.example" or . == "magpie-deploy.sh")] | length == 3)
        | .tag_name' |
        python3 -c '
import re, sys

def key(v):
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.]+))?", v.strip())
    if not m:
        return None
    pre = m.group(4)
    parts = [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.findall(r"\d+|[^\d.]+", pre or "")]
    # A final release sorts after its own prereleases.
    return (*map(int, m.group(1, 2, 3)), (1,) if pre is None else (0, *parts))

current = key(sys.argv[1])
older = [(key(t), t.strip()) for t in sys.stdin if key(t) and key(t) < current]
if older:
    print(max(older)[1].removeprefix("v"))
' "$VERSION"
}

case "${1:-}" in
assets) cmd_assets ;;
smoke) cmd_smoke ;;
compose) cmd_compose ;;
compose-upgrade) cmd_compose_upgrade ;;
installer) cmd_installer ;;
installer-upgrade) cmd_installer_upgrade ;;
previous) cmd_previous ;;
*) die "usage: release_verify.sh assets|smoke|compose|compose-upgrade|installer|installer-upgrade|previous" ;;
esac
