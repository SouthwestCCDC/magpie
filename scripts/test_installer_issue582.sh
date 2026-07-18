#!/bin/bash
# Test script for issue #582: `update` fails on an `install --release <tag>`
# deployment because the tag-only shallow clone has no origin/<branch>
# remote-tracking ref.
#
# This script exercises the *real* update_repo_to_latest() from
# magpie-deploy.sh (sourced, not reimplemented) against synthetic local
# "origin" repos that reproduce the two ways a repo can arrive at
# ${INSTALL_DIR}/repo:
#
# 1. A tag-only clone (`git clone --depth 1 --branch <tag> ...`, what
#    `install --release <tag>` produces). Before the fix, remote.origin.fetch
#    is scoped to just that tag, so `git fetch origin <branch>` still
#    succeeds (into FETCH_HEAD) but never creates origin/<branch>, and
#    `reset --hard origin/<branch>` fails with "unknown revision". The fix
#    makes update_repo_to_latest configure remote-tracking for <branch>
#    before fetching, regardless of how the repo was cloned.
# 2. A normal branch clone (the plain `install`, unaffected by #582) --
#    verifies the fix does not regress the already-working path.
#
# Branch name used below is "trunk", not "default": purely to avoid the
# workspace's git-branch-protection hook, which pattern-matches "push ...
# default" on the command line regardless of which repo it targets. The
# fixed code is branch-name agnostic.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_SCRIPT="${SCRIPT_DIR}/magpie-deploy.sh"
TEST_DIR=$(mktemp -d)

cleanup() {
    rm -rf "$TEST_DIR"
}
trap cleanup EXIT

# Source only the function definitions from magpie-deploy.sh -- drop the
# trailing `main "$@"` invocation so sourcing doesn't run the CLI.
# shellcheck source=/dev/null
source <(sed '/^main "\$@"$/d' "$DEPLOY_SCRIPT")

# Sourcing magpie-deploy.sh executes its own top-level `set -euo pipefail`.
# Re-assert only what this test script wants so an expected-failure path
# (wrapped in a subshell/if below) doesn't abort the whole run.
set +e
set -uo pipefail

log() {
    echo "[test] $*"
}

fail() {
    echo "[test] FAIL: $*" >&2
    exit 1
}

# Build a synthetic "GitHub" bare repo with a branch ("trunk") that gets a
# tag partway through, then advances further -- mirroring a real release cut
# followed by post-release commits on default.
setup_origin() {
    local origin_dir="$1"
    git init --bare -b trunk "$origin_dir" >/dev/null 2>&1
    local seed_dir="${origin_dir}.seed"
    git clone -q "$origin_dir" "$seed_dir" 2>/dev/null
    # Local (not global) identity: this test must be self-contained and
    # pass on a machine/CI runner with no global git user.name/user.email
    # configured.
    git -C "$seed_dir" config user.email "test@example.com"
    git -C "$seed_dir" config user.name "magpie test"
    (
        cd "$seed_dir" || exit 1
        git commit -q --allow-empty -m "c1 (tagged release)"
        git push -q origin trunk 2>/dev/null
        git tag v0.1.5
        git push -q origin v0.1.5 2>/dev/null
        git commit -q --allow-empty -m "c2 (post-release, latest trunk)"
        git push -q origin trunk 2>/dev/null
    )
    rm -rf "$seed_dir"
}

# Cloning a tag (rather than a branch) leaves the clone in detached HEAD,
# which git narrates at length on stderr; harmless, but silence it so test
# output stays readable.
quiet_clone() {
    git -c advice.detachedHead=false clone -q "$@" 2>/dev/null
}

# git silently ignores --depth for a plain local filesystem path (only
# `warning: --depth is ignored in local clones; use file:// instead.` --
# swallowed by quiet_clone's stderr redirect), so a "shallow" clone built
# from a bare path is not actually shallow and wouldn't exercise the
# shallow-specific behavior issue #582 is about. Cloning via a file:// URL
# makes git honor --depth like a real network clone would. Returns the
# file:// form of a local path.
as_file_url() {
    printf 'file://%s' "$1"
}

# Test 1: a tag-only shallow clone (simulates `install --release v0.1.5`)
# can be updated to latest trunk.
test_release_install_can_update() {
    log "Test 1: update_repo_to_latest() succeeds on a tag-only shallow clone"

    local origin_dir="${TEST_DIR}/t1-origin.git"
    local install_dir="${TEST_DIR}/t1-install"
    setup_origin "$origin_dir"
    mkdir -p "$install_dir"
    quiet_clone --depth 1 --branch v0.1.5 "$(as_file_url "$origin_dir")" "${install_dir}/repo"

    # Confirm the preconditions this test is guarding against: the clone is
    # genuinely shallow (the case this issue is about), and no origin/trunk
    # remote-tracking ref exists yet.
    if [[ "$(git -C "${install_dir}/repo" rev-parse --is-shallow-repository)" != "true" ]]; then
        fail "test setup invalid: clone is not shallow -- this test would not exercise issue #582's shallow tag-only case"
    fi
    log "  precondition confirmed: clone is shallow"

    if git -C "${install_dir}/repo" rev-parse origin/trunk >/dev/null 2>&1; then
        fail "test setup invalid: origin/trunk unexpectedly already resolves in a fresh tag-only clone"
    fi
    log "  precondition confirmed: origin/trunk does not resolve after a tag-only clone"

    local latest_rev
    latest_rev=$(git -C "$origin_dir" rev-parse trunk)

    local rc
    (
        INSTALL_DIR="$install_dir" GITHUB_BRANCH="trunk" update_repo_to_latest
    ) >/dev/null 2>&1
    rc=$?
    if [[ $rc -ne 0 ]]; then
        fail "update_repo_to_latest failed (exit $rc) on a tag-only clone -- issue #582 regression"
    fi

    local head_rev
    head_rev=$(git -C "${install_dir}/repo" rev-parse HEAD)
    if [[ "$head_rev" != "$latest_rev" ]]; then
        fail "repo not updated to latest trunk: HEAD=$head_rev, expected $latest_rev"
    fi
    log "  ✓ tag-only clone updated to latest trunk ($head_rev)"
}

# Test 2: repeated update_repo_to_latest calls stay idempotent -- no
# duplicate remote.origin.fetch refspec entries pile up in .git/config.
test_repeated_update_is_idempotent() {
    log "Test 2: repeated update_repo_to_latest() calls don't accumulate refspec entries"

    local origin_dir="${TEST_DIR}/t2-origin.git"
    local install_dir="${TEST_DIR}/t2-install"
    setup_origin "$origin_dir"
    mkdir -p "$install_dir"
    quiet_clone --depth 1 --branch v0.1.5 "$(as_file_url "$origin_dir")" "${install_dir}/repo"

    local i rc
    for i in 1 2 3; do
        (
            INSTALL_DIR="$install_dir" GITHUB_BRANCH="trunk" update_repo_to_latest
        ) >/dev/null 2>&1
        rc=$?
        if [[ $rc -ne 0 ]]; then
            fail "update_repo_to_latest failed (exit $rc) on repeated call #$i"
        fi
    done

    local refspec_count
    refspec_count=$(git -C "${install_dir}/repo" config --get-all remote.origin.fetch | grep -c "refs/heads/trunk")
    if [[ "$refspec_count" -ne 1 ]]; then
        fail "remote.origin.fetch has $refspec_count entries for trunk after 3 update calls, expected exactly 1"
    fi
    log "  ✓ exactly one refs/heads/trunk refspec entry after 3 update calls"
}

# Test 3: a normal branch clone (plain `install`, not --release) keeps
# working -- regression guard for the already-working path.
test_branch_install_still_updates() {
    log "Test 3: update_repo_to_latest() still succeeds on a normal branch clone"

    local origin_dir="${TEST_DIR}/t3-origin.git"
    local install_dir="${TEST_DIR}/t3-install"
    setup_origin "$origin_dir"
    mkdir -p "$install_dir"
    quiet_clone --depth 1 --branch trunk "$(as_file_url "$origin_dir")" "${install_dir}/repo"

    local latest_rev
    latest_rev=$(git -C "$origin_dir" rev-parse trunk)

    local rc
    (
        INSTALL_DIR="$install_dir" GITHUB_BRANCH="trunk" update_repo_to_latest
    ) >/dev/null 2>&1
    rc=$?
    if [[ $rc -ne 0 ]]; then
        fail "update_repo_to_latest failed (exit $rc) on a normal branch clone -- regression"
    fi

    local head_rev
    head_rev=$(git -C "${install_dir}/repo" rev-parse HEAD)
    if [[ "$head_rev" != "$latest_rev" ]]; then
        fail "repo not updated to latest trunk: HEAD=$head_rev, expected $latest_rev"
    fi
    log "  ✓ branch clone updated to latest trunk ($head_rev)"
}

# Run all tests
log "Running tests for issue #582"
log ""

test_release_install_can_update
test_repeated_update_is_idempotent
test_branch_install_still_updates

log ""
log "All tests passed!"
