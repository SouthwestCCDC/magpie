#!/bin/bash
# Decide which rolling image tags (latest, <major>, <major>.<minor>) a
# release may move. Used by .github/workflows/release.yml.
#
# Usage: git tag -l 'v*' | scripts/release_tag_flags.sh <version>
#
# Prints GITHUB_OUTPUT-style lines: major, minor, move_latest, move_major,
# move_minor. A rolling tag moves only for a stable X.Y.Z that is the
# highest stable release in that tag's scope. So a hotfix on an older line
# (e.g. 0.1.7 cut after 0.2.0) can't drag :latest or :0 back onto the
# older line, and a prerelease never moves any of them.
set -euo pipefail

version="${1:?usage: release_tag_flags.sh <version> < existing-tags}"
version="${version#v}"
major="${version%%.*}"
minor="$(cut -d. -f1-2 <<<"$version")"
echo "major=${major}"
echo "minor=${minor}"

stable_re='^[0-9]+\.[0-9]+\.[0-9]+$'
if [[ ! "$version" =~ $stable_re ]]; then
    printf '%s\n' move_latest=false move_major=false move_minor=false
    exit 0
fi

stable=("$version")
while IFS= read -r tag; do
    tag="${tag#v}"
    if [[ "$tag" =~ $stable_re ]]; then
        stable+=("$tag")
    fi
done

highest() { sort -V | tail -n1; }
top_all="$(printf '%s\n' "${stable[@]}" | highest)"
top_major="$(printf '%s\n' "${stable[@]}" | awk -F. -v ma="$major" '$1 == ma' | highest)"
top_minor="$(printf '%s\n' "${stable[@]}" | awk -F. -v mm="$minor" '$1 "." $2 == mm' | highest)"

is_top() { if [[ "$1" == "$version" ]]; then echo true; else echo false; fi; }
echo "move_latest=$(is_top "$top_all")"
echo "move_major=$(is_top "$top_major")"
echo "move_minor=$(is_top "$top_minor")"
