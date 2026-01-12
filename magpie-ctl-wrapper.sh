#!/bin/bash
# Unified wrapper for magpie-ctl that handles privilege management
#
# This wrapper detects whether it needs to drop privileges:
# - If already running as the correct user (UID matches), exec directly
# - If running as root, use gosu to drop privileges
# - Fallback to direct exec if /run/magpie-user doesn't exist or UID is neither expected nor root
#
# This enables magpie-ctl to work correctly whether invoked:
# - From within the running container (same user)
# - Via docker exec (typically as root)
# - From cron or other contexts

set -e

# Use an array so the command and its arguments are kept as separate elements
REAL_CTL=(/app/.venv/bin/python -m magpie.ctl)

if [ -f /run/magpie-user ]; then
    EXPECTED_UID=$(cut -d: -f1 /run/magpie-user)
    CURRENT_UID=$(id -u)

    # Validate that EXPECTED_UID is non-empty and numeric
    if [ -z "$EXPECTED_UID" ] || ! [[ "$EXPECTED_UID" =~ ^[0-9]+$ ]]; then
        echo "magpie-ctl-wrapper: invalid UID in /run/magpie-user: '$EXPECTED_UID'" >&2
        exit 1
    fi

    if [ "$CURRENT_UID" = "$EXPECTED_UID" ]; then
        exec "${REAL_CTL[@]}" "$@"
    elif [ "$CURRENT_UID" = "0" ]; then
        if command -v gosu >/dev/null 2>&1; then
            exec gosu "$(cat /run/magpie-user)" "${REAL_CTL[@]}" "$@"
        else
            echo "magpie-ctl-wrapper: gosu is required to drop privileges from root but was not found in PATH" >&2
            exit 1
        fi
    fi
    # If we reach here, the current UID is neither the expected UID nor root.
    # This is an unexpected state - the wrapper should only be invoked inside
    # the container where we control the user context. Fall through to the
    # final exec below, which will run as the current (unexpected) user.
    echo "magpie-ctl-wrapper: warning: running as UID $CURRENT_UID (expected $EXPECTED_UID or 0)" >&2
fi

# Fallback: run directly if /run/magpie-user doesn't exist (container started
# without entrypoint) or if we're in an unexpected UID state (see warning above).
exec "${REAL_CTL[@]}" "$@"
