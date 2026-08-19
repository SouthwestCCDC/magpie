#!/bin/bash
# Tier 1 of the installer CI (issue #430): `caddy validate` every Caddyfile
# this repo ships, under each environment-variable shape a real deployment
# can produce.
#
# Why this needs to run on every PR: the bundled image's Caddyfile
# (docker/bundled/Caddyfile) IS the auth gate -- forward_auth, the #525
# X-Magpie-* header strip, and the CIDR bypass all live in it. It is baked
# into the image at build time and Caddy is started by wrapper.sh as the
# container's supervisor, so a config Caddy rejects is not a degraded
# install, it's a container that never serves. Validating the adapted
# config statically costs seconds and needs no install.
#
# The env-var matrix matters as much as the file: {$MAGPIE_ALLOWED_CIDRS}
# and {$MAGPIE_TRUSTED_PROXIES} are substituted as literal text before the
# Caddyfile is adapted, so the SAME file can be valid with them unset (the
# 255.255.255.255/32 placeholder default) and invalid with an operator's
# value in place. Each case below is a shape .env can actually hold.
#
# NOTE (pre-existing, deliberately not changed here): Caddy's client_ip /
# trusted_proxies matchers take a WHITESPACE-separated list, while
# docker-compose.yml, .env.example, and src/magpie/config.py all document
# MAGPIE_ALLOWED_CIDRS as comma-separated ("10.0.0.0/8,192.168.1.0/24").
# A comma-separated multi-CIDR value makes Caddy fail to provision, so the
# matrix below uses the whitespace form Caddy actually accepts. See the
# issue linked from PR for this discrepancy; fixing it is a product
# decision outside this PR's scope.
#
# Usage: scripts/validate_caddyfiles.sh [Caddyfile ...]
#        MAGPIE_CADDY_IMAGE=caddy:2.11.4-alpine scripts/validate_caddyfiles.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

log() {
    echo "[caddy-validate] $*"
}

cd "$REPO_ROOT"

# Validate against the same Caddy version the bundled image builds with,
# read from Dockerfile.bundled rather than duplicated here -- a Caddy
# upgrade there must not silently leave this check on an older adapter.
CADDY_VERSION="$(sed -nE 's/^ARG CADDY_VERSION=(.+)$/\1/p' Dockerfile.bundled | head -n1)"
if [[ -z "$CADDY_VERSION" ]]; then
    echo "[caddy-validate] ERROR: could not read ARG CADDY_VERSION from Dockerfile.bundled" >&2
    exit 1
fi
CADDY_IMAGE="${MAGPIE_CADDY_IMAGE:-caddy:${CADDY_VERSION}-alpine}"

if ! command -v docker >/dev/null 2>&1; then
    echo "[caddy-validate] ERROR: docker is required (used to run ${CADDY_IMAGE})" >&2
    exit 1
fi

declare -a caddyfiles=()
if (( $# > 0 )); then
    caddyfiles=("$@")
else
    # Repo-relative, matching the paths an explicit argument is resolved
    # against below.
    while IFS= read -r -d '' file; do
        caddyfiles+=("$file")
    done < <(git ls-files -z 'Caddyfile*' '*/Caddyfile*')
fi

if (( ${#caddyfiles[@]} == 0 )); then
    echo "[caddy-validate] ERROR: no Caddyfiles found to validate" >&2
    exit 1
fi

# One entry per case: "<label>|<MAGPIE_ALLOWED_CIDRS>|<MAGPIE_TRUSTED_PROXIES>"
declare -a cases=(
    "unset (installer default: no CIDR bypass, trust no proxy)||"
    "single CIDR, single trusted proxy hop|172.18.0.0/24|172.20.0.1"
    "multiple CIDRs and proxy hops (whitespace-separated)|10.0.0.0/8 192.168.1.0/24|172.20.0.0/16 10.4.0.1"
    "IPv6 CIDR and proxy hop|2001:db8::/32|2001:db8::1"
)

log "caddy image: ${CADDY_IMAGE}"
log "validating ${#caddyfiles[@]} Caddyfile(s) against ${#cases[@]} env case(s)"

declare -a failures=()

for caddyfile in "${caddyfiles[@]}"; do
    # Bind mounts need an absolute source, and docker silently CREATES an empty
    # directory for one that does not exist -- so a mangled path fails as
    # "caddy validate on a directory" rather than "no such file". Resolve here
    # instead: repo-relative for the git ls-files default, left alone when the
    # caller passed an absolute path.
    if [[ "$caddyfile" == /* ]]; then
        caddyfile_src="$caddyfile"
    else
        caddyfile_src="${REPO_ROOT}/${caddyfile}"
    fi
    if [[ ! -f "$caddyfile_src" ]]; then
        echo "[caddy-validate] ERROR: not a file: ${caddyfile_src}" >&2
        exit 1
    fi

    for spec in "${cases[@]}"; do
        IFS='|' read -r label allowed_cidrs trusted_proxies <<< "$spec"
        log "${caddyfile}: ${label}"
        # --rm -i with the Caddyfile mounted read-only at the same path the
        # image expects; `caddy validate` adapts AND provisions the config
        # (the step that actually catches a bad matcher value), without
        # binding any port.
        if ! docker run --rm \
            -v "${caddyfile_src}:/etc/caddy/Caddyfile:ro" \
            -e "MAGPIE_ALLOWED_CIDRS=${allowed_cidrs}" \
            -e "MAGPIE_TRUSTED_PROXIES=${trusted_proxies}" \
            "$CADDY_IMAGE" \
            caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile; then
            failures+=("${caddyfile} [${label}]")
        fi
    done
done

if (( ${#failures[@]} > 0 )); then
    echo "" >&2
    echo "[caddy-validate] FAIL: invalid configuration:" >&2
    for failure in "${failures[@]}"; do
        echo "  - ${failure}" >&2
    done
    exit 1
fi

log "OK: all Caddyfiles valid across every env case"
