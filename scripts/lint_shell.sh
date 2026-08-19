#!/bin/bash
# Tier 1 of the installer CI (issue #430): static analysis of every shell
# script in the repo -- `bash -n` (parse) plus `shellcheck -x` (lint).
#
# The installer (scripts/magpie-deploy.sh) is the most security-critical
# and least-tested code in the repo, and the scripts baked into the
# bundled image (entrypoint.sh, docker/bundled/wrapper.sh,
# docker/bundled/healthcheck.sh, magpie-ctl-wrapper.sh) run as the
# container's supervisor. A syntax error or an unquoted expansion in any
# of them is a broken deployment, so this runs on every PR -- it needs no
# Docker, no systemd, and no install (see .github/workflows/installer.yml
# for the tiering).
#
# The enabled ruleset (and the three deliberately-disabled checks) lives
# in .shellcheckrc at the repo root, so this script, the pre-commit hook,
# and CI all report the same findings.
#
# Usage: scripts/lint_shell.sh [file ...]   (default: all tracked *.sh)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

log() {
    echo "[lint-shell] $*"
}

if ! command -v shellcheck >/dev/null 2>&1; then
    echo "[lint-shell] ERROR: shellcheck is not installed." >&2
    echo "  Debian/Ubuntu: sudo apt-get install -y shellcheck" >&2
    echo "  macOS:         brew install shellcheck" >&2
    exit 1
fi

cd "$REPO_ROOT"

declare -a files=()
if (( $# > 0 )); then
    files=("$@")
else
    # Tracked files only, so a stray script in an untracked scratch
    # directory never fails CI. -z/read -d handles paths with spaces.
    while IFS= read -r -d '' file; do
        files+=("$file")
    done < <(git ls-files -z '*.sh')
fi

if (( ${#files[@]} == 0 )); then
    echo "[lint-shell] ERROR: no shell scripts to check" >&2
    exit 1
fi

log "shellcheck $(shellcheck --version | sed -n 's/^version: //p')"
log "checking ${#files[@]} shell script(s)"

# Collect every failing file instead of aborting on the first one: a single
# CI run should report all of them. Each check below is invoked in an `if`
# so errexit stays on for the rest of the script.
declare -a parse_failures=()
declare -a lint_failures=()

for file in "${files[@]}"; do
    if ! bash -n "$file"; then
        parse_failures+=("$file")
    fi
done

for file in "${files[@]}"; do
    if ! shellcheck -x "$file"; then
        lint_failures+=("$file")
    fi
done

if (( ${#parse_failures[@]} > 0 || ${#lint_failures[@]} > 0 )); then
    echo "" >&2
    if (( ${#parse_failures[@]} > 0 )); then
        echo "[lint-shell] FAIL: bash -n rejected: ${parse_failures[*]}" >&2
    fi
    if (( ${#lint_failures[@]} > 0 )); then
        echo "[lint-shell] FAIL: shellcheck findings in: ${lint_failures[*]}" >&2
    fi
    exit 1
fi

log "OK: ${#files[@]} script(s) pass bash -n and shellcheck"
