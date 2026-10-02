#!/bin/bash
# Point every rolling tag of <image> at the highest published stable
# release in its scope. Run by the promote job in
# .github/workflows/release.yml after `git fetch --tags`.
#
# Usage: scripts/release_promote.sh <image>   (e.g. ghcr.io/southwestccdc/magpie)
#
# "Published" means the <version> image exists. Any inspect failure other
# than "not found" aborts before an alias moves, so a registry error can't
# make a newer release look absent.
set -euo pipefail

image="${1:?usage: release_promote.sh <image>}"
here="$(cd "$(dirname "$0")" && pwd)"

published=()
while read -r tag; do
    v="${tag#v}"
    if err="$(docker buildx imagetools inspect "${image}:${v}" 2>&1 >/dev/null)"; then
        published+=("$v")
    elif [[ "$err" == *": not found" ]]; then
        echo "Skipping ${v}: no image in the registry"
    else
        echo "::error::Could not inspect ${image}:${v}; not moving any alias: ${err}"
        exit 1
    fi
done < <(git tag -l 'v*' | { grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' || true; })

aliases="$(printf '%s\n' "${published[@]}" | "${here}/release_rolling_tags.sh")"
while read -r alias v; do
    [[ -n "$alias" ]] || continue
    echo "${alias} -> ${v}"
    docker buildx imagetools create -t "${image}:${alias}" "${image}:${v}"
done <<<"$aliases"
