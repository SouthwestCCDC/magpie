#!/bin/bash
set -e

# Determine UID/GID to run as:
# 1. Use MAGPIE_UID/MAGPIE_GID environment variables if set
# 2. Otherwise, detect from /data directory ownership (parent of both artifacts/ and magpie.db)
# 3. Fall back to 1000:1000 if /data is missing or stat on /data fails

if [ -n "$MAGPIE_UID" ]; then
    RUN_UID="$MAGPIE_UID"
else
    RUN_UID=$(stat -c %u /data 2>/dev/null || echo 1000)
fi

if [ -n "$MAGPIE_GID" ]; then
    RUN_GID="$MAGPIE_GID"
else
    RUN_GID=$(stat -c %g /data 2>/dev/null || echo 1000)
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
# Uses a lock file (held via flock on FD 200) to prevent race conditions when multiple
# containers share /data/artifacts. The lock is held for the entire subshell duration
# (both the existence check and the init command) and released when the subshell exits.
# The lock file persists on disk as an empty marker; concurrent entrypoints serialize
# correctly. A 30-second timeout prevents indefinite blocking.

# Determine the storage path for the lock file. We need a directory that exists and is
# writable. Check MAGPIE_STORAGE_PATH first, then fall back to /data/artifacts, then /data.
if [ -n "$MAGPIE_STORAGE_PATH" ] && [ -d "$MAGPIE_STORAGE_PATH" ]; then
    LOCK_DIR="$MAGPIE_STORAGE_PATH"
elif [ -d /data/artifacts ]; then
    LOCK_DIR=/data/artifacts
elif [ -d /data ]; then
    LOCK_DIR=/data
else
    echo "Error: No valid storage directory found for lock file (tried MAGPIE_STORAGE_PATH, /data/artifacts, /data)" >&2
    exit 1
fi
DB_LOCK_FILE="${LOCK_DIR}/.magpie-init.lock"

# Determine database path: MAGPIE_DATABASE_PATH takes precedence, otherwise default to /data/magpie.db
# NOTE: The database path is independent of LOCK_DIR. The lock file resides in the storage
# directory to coordinate initialization, but the database has its own fixed location.
if [ -n "$MAGPIE_DATABASE_PATH" ]; then
    DB_PATH="$MAGPIE_DATABASE_PATH"
else
    # Default to /data/magpie.db (matches Python default in config.py)
    DB_PATH="/data/magpie.db"
fi

# Export MAGPIE_DATABASE_PATH so magpie-ctl init uses the correct path
export MAGPIE_DATABASE_PATH="$DB_PATH"

# Verify gosu is available before we need it (only required when not running as root)
if [ "$RUN_UID" != "0" ] && ! command -v gosu >/dev/null 2>&1; then
    echo "Error: gosu is required to drop privileges but was not found in PATH" >&2
    exit 1
fi

# Acquire the lock and check/initialize the database atomically
# The flock subshell holds the lock for the entire duration of the init check.
# Using -w 30 to timeout after 30 seconds instead of blocking indefinitely.
(
    flock_status=0
    flock -x -w 30 200 || flock_status=$?
    if [ "$flock_status" -ne 0 ]; then
        if [ "$flock_status" -eq 1 ]; then
            echo "Error: Failed to acquire database init lock on $DB_LOCK_FILE within 30 seconds (another process may be initializing the database)" >&2
        else
            echo "Error: flock failed with exit code $flock_status while trying to lock $DB_LOCK_FILE (is flock available and working?)" >&2
        fi
        exit 1
    fi

    if [ ! -f "$DB_PATH" ]; then
        echo "Database not found at $DB_PATH, running magpie-ctl init..."
        init_status=0
        if [ "$RUN_UID" = "0" ]; then
            /app/.venv/bin/python -m magpie.ctl init || init_status=$?
        else
            gosu "$RUN_UID:$RUN_GID" /app/.venv/bin/python -m magpie.ctl init || init_status=$?
        fi
        if [ "$init_status" -ne 0 ]; then
            echo "Error: magpie-ctl init failed with exit code $init_status" >&2
            # Remove potentially incomplete database file to allow retry on next start
            if [ -f "$DB_PATH" ]; then
                echo "Removing incomplete database file at $DB_PATH" >&2
                rm -f "$DB_PATH"
            fi
            exit 1
        fi
    fi
) 200>"$DB_LOCK_FILE"

# Run as root if UID is 0 (no privilege drop needed)
if [ "$RUN_UID" = "0" ]; then
    exec "$@"
fi

# Drop privileges and execute the command
exec gosu "$RUN_UID:$RUN_GID" "$@"
