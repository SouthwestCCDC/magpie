#!/bin/bash
# Point every rolling tag of <image> at the highest published stable
# release in its scope. Run by the promote job in
# .github/workflows/release.yml after `git fetch --tags`, with GH_TOKEN and
# GITHUB_REPOSITORY set.
#
# Usage: scripts/release_promote.sh <image> <verified-version>
#   e.g. scripts/release_promote.sh ghcr.io/southwestccdc/magpie 0.2.1
#
# "Published" means the <version> image exists AND either the tag has a
# published GitHub Release or it is <verified-version>, the release this
# run just verified (its GitHub Release is created after promotion). A tag
# whose verification failed has an image but no Release, so it never gets
# an alias. Any inspect or API failure other than "not found" aborts before
# an alias moves, so an error can't make a newer release look absent.
set -euo pipefail

image="${1:?usage: release_promote.sh <image> <verified-version>}"
verified="${2:?usage: release_promote.sh <image> <verified-version>}"
here="$(cd "$(dirname "$0")" && pwd)"

released="$(gh release list --repo "${GITHUB_REPOSITORY:?}" --limit 1000 \
    --exclude-drafts --json tagName --jq '.[].tagName')"

published=()
while read -r tag; do
    v="${tag#v}"
    if [[ "$v" != "$verified" ]] && ! grep -qxF "$tag" <<<"$released"; then
        echo "Skipping ${v}: no published GitHub Release"
        continue
    fi
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
