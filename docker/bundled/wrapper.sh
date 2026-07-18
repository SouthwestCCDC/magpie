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

caddy run --config /etc/caddy/Caddyfile --adapter caddyfile &
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
