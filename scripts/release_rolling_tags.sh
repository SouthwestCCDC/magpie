#!/bin/bash
# Compute where every rolling image tag should point. Used by the promote
# job in .github/workflows/release.yml.
#
# Usage: printf '%s\n' <published stable versions> | scripts/release_rolling_tags.sh
#
# Prints "<alias> <version>" lines: `latest` -> the highest version,
# `<major>` -> the highest in each major, `<major>.<minor>` -> the highest
# in each minor. Each run reconciles every alias from the full list, so an
# older-line hotfix, an out-of-order run, or a skipped run can't leave an
# alias on the wrong release. Non-X.Y.Z input (prereleases) is ignored.
set -euo pipefail

{ grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+$' || true; } |
    sed 's/^v//' | sort -u -V |
    awk -F. '
        { latest = $0; major[$1] = $0; minor[$1 "." $2] = $0 }
        END {
            if (latest != "") print "latest", latest
            for (k in major) print k, major[k]
            for (k in minor) print k, minor[k]
        }' |
    sort -V
