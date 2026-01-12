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
fi

exec "${REAL_CTL[@]}" "$@"
