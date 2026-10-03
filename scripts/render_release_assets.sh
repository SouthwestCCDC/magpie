#!/bin/bash
# Render the files attached to a GitHub release (#633): docker-compose.yml
# and env.example with the image pinned to this release by digest, the
# installer, and SHA256SUMS over all of them. Used by
# .github/workflows/release.yml; runnable locally for testing.
#
# Usage: scripts/render_release_assets.sh <version> <sha256:digest> <outdir>
set -euo pipefail

version="${1:?usage: render_release_assets.sh <version> <digest> <outdir>}"
digest="${2:?missing image digest}"
outdir="${3:?missing output directory}"
# The templates always name the upstream image; IMAGE_REPO only picks what
# the rendered assets point at, so forks pin their own image.
template_repo="ghcr.io/southwestccdc/magpie"
image_repo="${IMAGE_REPO:-$template_repo}"

if [[ ! "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "render_release_assets: not a sha256 digest: '${digest}'" >&2
    exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pinned="${image_repo}:${version}@${digest}"
mkdir -p "$outdir"

# Each substitution must match exactly once, so drift in the source files
# fails the release instead of shipping an unpinned asset.
render() {
    local src="$1" dest="$2" pattern="$3" replacement="$4" count
    count="$(grep -cF -- "$pattern" "$src" || true)"
    if [[ "$count" != 1 ]]; then
        echo "render_release_assets: expected '${pattern}' once in ${src}, found ${count}" >&2
        exit 1
    fi
    while IFS= read -r line || [[ -n "$line" ]]; do
        printf '%s\n' "${line//"$pattern"/"$replacement"}"
    done <"$src" >"$dest"
}

render "${repo_root}/docker-compose.yml" "${outdir}/docker-compose.yml.tmp" \
    "\${MAGPIE_IMAGE:-${template_repo}:latest}" "\${MAGPIE_IMAGE:-${pinned}}"
render "${outdir}/docker-compose.yml.tmp" "${outdir}/docker-compose.yml" \
    "(default: ${template_repo}:latest)" "(default: ${pinned})"
rm "${outdir}/docker-compose.yml.tmp"

render "${repo_root}/.env.example" "${outdir}/.env.example.tmp" \
    "# Default: ${template_repo}:latest" "# Default: ${pinned}"
render "${outdir}/.env.example.tmp" "${outdir}/env.example" \
    "# MAGPIE_IMAGE=${template_repo}:latest" "# MAGPIE_IMAGE=${pinned}"
rm "${outdir}/.env.example.tmp"

cp "${repo_root}/scripts/magpie-deploy.sh" "${outdir}/magpie-deploy.sh"

# GitHub renames release assets with a leading dot (".env.example" is
# published as "default.env.example"), so the template ships without one.
(cd "$outdir" && sha256sum docker-compose.yml env.example magpie-deploy.sh >SHA256SUMS)
echo "Rendered release assets for ${pinned} in ${outdir}"
