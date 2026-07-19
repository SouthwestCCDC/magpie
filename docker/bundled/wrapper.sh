#!/bin/bash
# Magpie bundled single-container image -- process supervisor.
#
# Runs under tini (PID 1, see Dockerfile.bundled). Starts uvicorn, waits for
# it to answer /health, then starts caddy. Both processes fail fast: either
# one exiting on its own brings down the whole container with a non-zero
# exit code, so an orchestrator restarts the full unit rather than leaving
# it in a half-healthy state (auth gate up with no backend, or vice versa).
#
# A `docker stop`/SIGTERM is NOT a failure: it is forwarded to both children
# so they drain in-flight requests, and the container exits 0.
set -u

UVICORN_PID=""
CADDY_PID=""
SHUTTING_DOWN=0

log() {
	echo "[wrapper] $*" >&2
}

# On SIGTERM/SIGINT (docker stop, or tini forwarding a signal sent to the
# container), forward SIGTERM to both children so they drain in-flight
# requests, and mark this as a requested shutdown so the final exit code is
# 0 rather than the fail-fast non-zero code.
# shellcheck disable=SC2317  # only reachable via the trap below
term_handler() {
	if [ "$SHUTTING_DOWN" -eq 0 ]; then
		SHUTTING_DOWN=1
		log "received termination signal, draining both processes"
		[ -n "$UVICORN_PID" ] && kill -TERM "$UVICORN_PID" 2>/dev/null
		[ -n "$CADDY_PID" ] && kill -TERM "$CADDY_PID" 2>/dev/null
	fi
}
trap term_handler TERM INT

# Start uvicorn via the existing entrypoint (DB init lock + privilege drop
# via gosu); entrypoint.sh's final step is `exec`, so $UVICORN_PID ends up
# being uvicorn itself, not a wrapper shell -- SIGTERM sent to it reaches
# uvicorn directly.
/entrypoint.sh /app/.venv/bin/uvicorn magpie.server.app:app \
	--host 127.0.0.1 --port 8000 &
UVICORN_PID=$!
# Close the narrow race where term_handler ran between the `&` above and
# this assignment: term_handler's own kill would have been a no-op then
# (UVICORN_PID was still empty), so catch up now that the PID is known --
# otherwise a later `wait "$UVICORN_PID"` could block on a process that was
# never actually signaled, hanging `docker stop` until Docker's own SIGKILL
# timeout.
if [ "$SHUTTING_DOWN" -eq 1 ]; then
	kill -TERM "$UVICORN_PID" 2>/dev/null
fi
log "uvicorn started, pid=$UVICORN_PID"

# Startup-ordering gate: block until uvicorn answers /health, so caddy never
# starts accepting connections while the backend is still coming up (no 502
# window).
#
# A termination signal can arrive during this window too (e.g. a fast
# `docker stop` right after `docker run`): term_handler above already sent
# uvicorn SIGTERM, so both exit branches below must check $SHUTTING_DOWN
# first and reap-and-exit-0 (graceful) instead of falling through to the
# fail-fast exit 1 -- otherwise a legitimate stop during startup gets
# misreported as a crash.
#
# --timeout bounds each individual wget connection attempt (DNS/connect/
# read combined): without it, a stalled connection (TCP connects but the
# response never arrives) could block wget indefinitely. --tries=1 is
# required alongside it: wget retries internally by default (20 times) for
# anything that isn't a fatal error like connection-refused -- and a
# timeout is exactly the kind of failure it WOULD retry, so without
# --tries=1 a single call could silently retry for up to 20 * 3s = 60s
# before returning, defeating both the max_attempts ceiling below and this
# loop's own signal responsiveness. Because each attempt can now take
# anywhere from ~0s (instant refusal/response) up to the 3s timeout,
# attempts is a bound on RETRIES, not wall-clock time -- track actual
# elapsed seconds separately via bash's SECONDS for accurate logging.
attempts=0
max_attempts=60
SECONDS=0
until wget -q -O /dev/null --timeout=3 --tries=1 http://127.0.0.1:8000/health 2>/dev/null; do
	if [ "$SHUTTING_DOWN" -eq 1 ]; then
		log "termination requested during startup, waiting for uvicorn to drain"
		wait "$UVICORN_PID" 2>/dev/null
		log "graceful shutdown complete (during startup)"
		exit 0
	fi
	if ! kill -0 "$UVICORN_PID" 2>/dev/null; then
		log "uvicorn exited before becoming ready"
		wait "$UVICORN_PID"
		exit 1
	fi
	attempts=$((attempts + 1))
	if [ "$attempts" -ge "$max_attempts" ]; then
		if [ "$SHUTTING_DOWN" -eq 1 ]; then
			log "termination requested during startup, waiting for uvicorn to drain"
			wait "$UVICORN_PID" 2>/dev/null
			log "graceful shutdown complete (during startup)"
			exit 0
		fi
		log "uvicorn did not become ready after ${attempts} attempts (${SECONDS}s elapsed)"
		kill -TERM "$UVICORN_PID" 2>/dev/null
		wait "$UVICORN_PID" 2>/dev/null
		exit 1
	fi
	sleep 1
done
log "uvicorn ready after ${attempts} attempts (${SECONDS}s elapsed)"

# Run Caddy as the same non-root user entrypoint.sh dropped uvicorn's
# privileges to (issue #589) -- not a fixed 1000:1000: entrypoint.sh
# derives RUN_UID/RUN_GID from MAGPIE_UID/MAGPIE_GID or from /data's
# ownership, and publishes the result to /run/magpie-user for exactly
# this purpose (see magpie-ctl-wrapper.sh, which reads the same file for
# `docker exec` invocations). Using the same uid as uvicorn also
# satisfies #589's requirement that Caddy can read what uvicorn writes
# under /data. entrypoint.sh writes this file before it execs uvicorn,
# and uvicorn is already answering /health above, so it is guaranteed to
# exist here.
# Bails out before Caddy ever starts: drains uvicorn (already up at this
# point) and exits 1, same fail-fast contract as the rest of this script.
# wrapper.sh runs under `set -u`, not `set -e`, so none of the setup below
# would stop the script on its own -- each step must check its own result.
fail_before_caddy() {
	log "error: $1"
	kill -TERM "$UVICORN_PID" 2>/dev/null
	wait "$UVICORN_PID" 2>/dev/null
	exit 1
}

if [ ! -f /run/magpie-user ]; then
	fail_before_caddy "/run/magpie-user not found (entrypoint.sh should have written it before uvicorn became ready)"
fi
CADDY_USER_CONTENT=$(cat /run/magpie-user)
CADDY_UID=${CADDY_USER_CONTENT%%:*}
CADDY_GID=${CADDY_USER_CONTENT##*:}
if ! [[ "$CADDY_UID" =~ ^[0-9]+$ ]] || ! [[ "$CADDY_GID" =~ ^[0-9]+$ ]]; then
	fail_before_caddy "invalid uid:gid in /run/magpie-user: '$CADDY_USER_CONTENT'"
fi

# Caddy writes an autosave config (and its own data dir) on every config
# load even with `admin off`. Give it a home outside the /data artifact
# volume, owned by the same uid:gid it's about to run as -- chowned here
# rather than at build time because CADDY_UID/GID are only known at
# runtime. A silent failure here would let Caddy start anyway and only
# fail later trying to write its autosave file (worse under reduced
# capabilities, where the chown itself is more likely to fail) -- check
# both explicitly instead.
if ! mkdir -p /var/lib/caddy/data /var/lib/caddy/config; then
	fail_before_caddy "failed to create /var/lib/caddy/{data,config}"
fi
if ! chown -R "$CADDY_UID:$CADDY_GID" /var/lib/caddy; then
	fail_before_caddy "failed to chown /var/lib/caddy to $CADDY_UID:$CADDY_GID"
fi
export XDG_DATA_HOME=/var/lib/caddy/data
export XDG_CONFIG_HOME=/var/lib/caddy/config

gosu "$CADDY_UID:$CADDY_GID" caddy run --config /etc/caddy/Caddyfile --adapter caddyfile &
CADDY_PID=$!
# Same race as uvicorn's above: catch up on a signal that arrived before
# CADDY_PID was captured, so a later `wait "$CADDY_PID"` can't hang on an
# unsignaled process.
if [ "$SHUTTING_DOWN" -eq 1 ]; then
	kill -TERM "$CADDY_PID" 2>/dev/null
fi
log "caddy started, pid=$CADDY_PID"

# Block until whichever child exits first (or until a trapped signal
# interrupts this wait -- see term_handler above).
wait -n
FIRST_EXIT=$?

if [ "$SHUTTING_DOWN" -eq 1 ]; then
	# A signal (e.g. `docker stop`) already told both children to drain.
	# Reap whichever one hasn't been waited on yet and exit 0: a normal,
	# requested shutdown, not a failure.
	wait "$UVICORN_PID" 2>/dev/null
	wait "$CADDY_PID" 2>/dev/null
	log "graceful shutdown complete"
	exit 0
fi

# One process exited on its own -- crash, or something killed it directly
# (e.g. `docker exec ... kill <pid>`). Fail fast: bring the other one down
# too and exit non-zero so an orchestrator restarts the whole container.
if kill -0 "$UVICORN_PID" 2>/dev/null; then
	log "caddy exited (code=$FIRST_EXIT) - killing uvicorn, failing container"
	kill -TERM "$UVICORN_PID" 2>/dev/null
	wait "$UVICORN_PID" 2>/dev/null
elif kill -0 "$CADDY_PID" 2>/dev/null; then
	log "uvicorn exited (code=$FIRST_EXIT) - killing caddy, failing container"
	kill -TERM "$CADDY_PID" 2>/dev/null
	wait "$CADDY_PID" 2>/dev/null
else
	log "both processes already gone (code=$FIRST_EXIT) - failing container"
fi

exit 1
