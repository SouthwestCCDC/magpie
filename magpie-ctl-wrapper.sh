#!/bin/bash
# Unified wrapper for magpie-ctl that handles privilege management
#
# This wrapper detects whether it needs to drop privileges:
# - If already running as the correct user (UID matches), exec directly
# - If running as root, use gosu to drop privileges
# - Fallback to direct exec if /run/magpie-user doesn't exist
#
# This enables magpie-ctl to work correctly whether invoked:
# - From within the running container (same user)
# - Via docker exec (typically as root)
# - From cron or other contexts

set -e

REAL_CTL="/app/.venv/bin/python -m magpie.ctl"

if [ -f /run/magpie-user ]; then
    EXPECTED_UID=$(cut -d: -f1 /run/magpie-user)
    CURRENT_UID=$(id -u)

    if [ "$CURRENT_UID" = "$EXPECTED_UID" ]; then
        exec $REAL_CTL "$@"
    elif [ "$CURRENT_UID" = "0" ]; then
        exec gosu "$(cat /run/magpie-user)" $REAL_CTL "$@"
    fi
fi

exec $REAL_CTL "$@"
