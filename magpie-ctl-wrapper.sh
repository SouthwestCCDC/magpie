#!/bin/bash
# Wrapper for magpie-ctl that ensures it runs with the same user as the main app
# This is needed when invoking magpie-ctl via docker exec, which bypasses the entrypoint

set -e

if [ -f /run/magpie-user ]; then
    # Read the UID:GID that the entrypoint determined
    USER_INFO=$(cat /run/magpie-user)
    exec gosu "$USER_INFO" /app/.venv/bin/magpie-ctl "$@"
else
    # Fallback: run directly (happens if container started without entrypoint)
    exec /app/.venv/bin/magpie-ctl "$@"
fi
