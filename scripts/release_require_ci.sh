#!/bin/bash
# Wait for CI on the commit a release tag points at, and fail unless every
# required check passed. Used by .github/workflows/release.yml (validate) so
# nothing is pushed or published from a commit CI hasn't passed.
#
# The required checks are the jobs a push to `default` runs (ci.yml,
# e2e.yml, installer.yml, post-merge.yml), so the tag has to be on a commit
# that was pushed to `default`.
#
# Usage: scripts/release_require_ci.sh <commit-sha>
# Env: GITHUB_REPOSITORY, GH_TOKEN (checks: read),
#      RELEASE_CI_TIMEOUT (seconds, default 3600), RELEASE_CI_POLL (default 30)
set -euo pipefail

sha="${1:?usage: release_require_ci.sh <commit-sha>}"
repo="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is not set}"
timeout="${RELEASE_CI_TIMEOUT:-3600}"
poll="${RELEASE_CI_POLL:-30}"

required=(
    lint
    test
    security
    e2e
    "Tier 1 -- static checks"
    "Tier 2 -- real install + purge"
    full-test-suite
)

join() {
    local IFS=, s
    s="$*"
    echo "${s//,/, }"
}

deadline=$((SECONDS + timeout))
while :; do
    # filter=latest (the API default) lists only the newest run per check
    # name, so a re-run that passed replaces the failure it re-ran.
    if ! runs="$(gh api --paginate "repos/${repo}/commits/${sha}/check-runs?per_page=100&filter=latest" \
        --jq '.check_runs[] | [.name, .status, (.conclusion // "")] | @tsv')"; then
        runs=""
        echo "Could not list check runs for ${sha}; retrying" >&2
    fi
    pending=()
    failed=()
    for name in "${required[@]}"; do
        lines="$(awk -F'\t' -v n="$name" '$1 == n' <<<"$runs")"
        if [[ -z "$lines" ]] || grep -qv $'\tcompleted\t' <<<"$lines"; then
            pending+=("$name")
        elif grep -qv $'\tcompleted\tsuccess$' <<<"$lines"; then
            failed+=("$name")
        fi
    done
    if ((${#failed[@]})); then
        echo "::error::CI did not pass on ${sha}: $(join "${failed[@]}")"
        exit 1
    fi
    if ((${#pending[@]} == 0)); then
        echo "CI passed on ${sha}: $(join "${required[@]}")"
        exit 0
    fi
    if ((SECONDS >= deadline)); then
        echo "::error::Timed out waiting for CI on ${sha}: $(join "${pending[@]}"). Required checks only run on pushes to default; is the tag on a commit in default?"
        exit 1
    fi
    echo "Waiting for: $(join "${pending[@]}")"
    sleep "$poll"
done
