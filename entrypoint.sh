#!/bin/bash
set -e

# Determine UID/GID to run as:
# 1. Use MAGPIE_UID/MAGPIE_GID environment variables if set
# 2. Otherwise, detect from /data directory ownership
# 3. Fall back to 1000:1000 if /data doesn't exist

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

# Run as root if UID is 0 (no privilege drop needed)
if [ "$RUN_UID" = "0" ]; then
    exec "$@"
fi

# Save the UID:GID for wrapper scripts invoked via docker exec
mkdir -p /run
echo "$RUN_UID:$RUN_GID" > /run/magpie-user

# Drop privileges and execute the command
exec gosu "$RUN_UID:$RUN_GID" "$@"
