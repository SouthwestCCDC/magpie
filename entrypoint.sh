#!/bin/bash
set -e

# Determine UID/GID to run as:
# 1. Use MAGPIE_UID/MAGPIE_GID environment variables if set
# 2. Otherwise, detect from /data/artifacts (docker-compose mount) or /data directory ownership
# 3. Fall back to 1000:1000 if /data is missing or stat on /data fails

if [ -n "$MAGPIE_UID" ]; then
    RUN_UID="$MAGPIE_UID"
else
    RUN_UID=$(stat -c %u /data/artifacts 2>/dev/null || stat -c %u /data 2>/dev/null || echo 1000)
fi

if [ -n "$MAGPIE_GID" ]; then
    RUN_GID="$MAGPIE_GID"
else
    RUN_GID=$(stat -c %g /data/artifacts 2>/dev/null || stat -c %g /data 2>/dev/null || echo 1000)
fi

# Validate that RUN_UID and RUN_GID are positive integers
if ! [[ "$RUN_UID" =~ ^[0-9]+$ ]] || [ "$RUN_UID" -lt 0 ]; then
    echo "Error: Invalid RUN_UID=$RUN_UID" >&2
    exit 1
fi

if ! [[ "$RUN_GID" =~ ^[0-9]+$ ]] || [ "$RUN_GID" -lt 0 ]; then
    echo "Error: Invalid RUN_GID=$RUN_GID" >&2
    exit 1
fi

# Save the UID:GID for wrapper scripts invoked via docker exec
# (Create this before root check so file always exists)
mkdir -p /run
echo "$RUN_UID:$RUN_GID" > /run/magpie-user
chmod 644 /run/magpie-user

# Auto-initialize database if it doesn't exist
# This runs before starting the main service
# Uses a lock file to prevent race conditions when multiple containers share /data
DB_LOCK_FILE=/data/.magpie-init.lock

(
    flock -x 200

    if [ ! -f /data/magpie.db ]; then
        echo "Database not found, running magpie-ctl init..."
        set +e
        if [ "$RUN_UID" = "0" ]; then
            /app/.venv/bin/python -m magpie.ctl init
            INIT_STATUS=$?
        else
            gosu "$RUN_UID:$RUN_GID" /app/.venv/bin/python -m magpie.ctl init
            INIT_STATUS=$?
        fi
        set -e

        if [ "$INIT_STATUS" -ne 0 ]; then
            echo "Error: magpie-ctl init failed with exit code $INIT_STATUS" >&2
            exit "$INIT_STATUS"
        fi
    fi
) 200>"$DB_LOCK_FILE"

# Run as root if UID is 0 (no privilege drop needed)
if [ "$RUN_UID" = "0" ]; then
    exec "$@"
fi

# Drop privileges and execute the command
exec gosu "$RUN_UID:$RUN_GID" "$@"
