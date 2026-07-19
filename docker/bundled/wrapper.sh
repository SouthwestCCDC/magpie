#!/bin/bash
# Magpie bundled single-container image -- process supervisor.
#
# Runs under tini (PID 1, see Dockerfile.bundled). tini execs this script
# directly; a brief ROOT PRELUDE (the on-disk ownership fixups only root
# can do -- chown /data, chown /var/lib/caddy) runs first, and this same
# script then re-execs itself dropped to the non-root runtime uid via
# gosu. Everything from that point on -- DB init, uvicorn, caddy, and the
# rest of this supervisor -- runs as that uid, not root (issue #591): no
# process in this container runs as root in steady state except tini
# itself (PID 1, kept root purely for zombie reaping, which needs no
# privilege). uvicorn and caddy are launched directly (no per-child gosu)
# since the whole supervisor is already at the target uid by then.
#
# The root prelude intentionally duplicates a small amount of
# entrypoint.sh's setup logic (uid resolution, /data ownership fixups)
# rather than calling entrypoint.sh: entrypoint.sh is also the ENTRYPOINT
# for the separate two-container `magpie` image, and its contract for
# that image must not change here (issue #591's PR description has the
# full rationale).
#
# Once running (post-drop), starts uvicorn, waits for it to answer
# /health, then starts caddy. Both processes fail fast: either one
# exiting on its own brings down the whole container with a non-zero
# exit code, so an orchestrator restarts the full unit rather than
# leaving it in a half-healthy state (auth gate up with no backend, or
# vice versa).
#
# A `docker stop`/SIGTERM is NOT a failure: it is forwarded to both
# children so they drain in-flight requests, and the container exits 0 --
# including a stop that arrives during the (very brief) root prelude
# itself, before anything has started.
set -u

log() {
	echo "[wrapper] $*" >&2
}

# Resolves STORAGE_DIR, LOCK_DIR, DB_LOCK_FILE, and DB_PATH from the
# MAGPIE_STORAGE_PATH / MAGPIE_DATABASE_PATH env vars -- the same
# resolution entrypoint.sh uses for the two-container image. Creates
# STORAGE_DIR and DB_DIR if they don't exist yet (this part doesn't
# require root: creating a directory only needs write access to its
# parent, and after the root prelude's chown, the post-drop uid has
# that). Sets STORAGE_DIR_CREATED=1 iff this call created STORAGE_DIR,
# for magpie_fixup_ownership below to decide whether to chown it.
# Idempotent and safe to call from both the root prelude (to learn what
# needs chowning) and the post-drop phase (to re-resolve the same paths,
# which is a no-op if the root prelude already ran).
STORAGE_DIR_CREATED=0
magpie_resolve_storage_paths() {
	STORAGE_DIR="${MAGPIE_STORAGE_PATH:-/data/artifacts}"
	if [ ! -d "$STORAGE_DIR" ]; then
		if ! mkdir -p "$STORAGE_DIR"; then
			log "error: failed to create storage directory $STORAGE_DIR"
			exit 1
		fi
		STORAGE_DIR_CREATED=1
	fi

	if [ -n "${MAGPIE_STORAGE_PATH:-}" ] && [ -d "$MAGPIE_STORAGE_PATH" ]; then
		LOCK_DIR="$MAGPIE_STORAGE_PATH"
	elif [ -d /data/artifacts ]; then
		LOCK_DIR=/data/artifacts
	elif [ -d /data ]; then
		LOCK_DIR=/data
	else
		log "error: no valid storage directory found for lock file (tried MAGPIE_STORAGE_PATH, /data/artifacts, /data)"
		exit 1
	fi
	DB_LOCK_FILE="${LOCK_DIR}/.magpie-init.lock"

	DB_PATH="${MAGPIE_DATABASE_PATH:-/data/magpie.db}"
	case "$DB_PATH" in
		/*) ;;
		*)
			log "error: MAGPIE_DATABASE_PATH must be an absolute path (got: $DB_PATH)"
			exit 1
			;;
	esac
	export MAGPIE_DATABASE_PATH="$DB_PATH"

	DB_DIR="$(dirname "$DB_PATH")"
	if [ ! -d "$DB_DIR" ]; then
		if ! mkdir -p "$DB_DIR"; then
			log "error: failed to create database directory $DB_DIR"
			exit 1
		fi
	fi
}

# Root-only: chowns whatever magpie_resolve_storage_paths determined
# needs it, plus /var/lib/caddy (#589's Caddy home, baked into the image
# root-owned). STORAGE_DIR is chowned only if this boot just created it
# (a pre-existing bind mount's ownership is left alone, matching
# entrypoint.sh); DB_DIR is chowned whenever its current ownership
# doesn't already match RUN_UID:RUN_GID, regardless of whether it was
# just created (also matching entrypoint.sh).
magpie_fixup_ownership() {
	if [ "$STORAGE_DIR_CREATED" -eq 1 ]; then
		if ! chown "$RUN_UID:$RUN_GID" "$STORAGE_DIR"; then
			log "error: failed to chown storage directory $STORAGE_DIR to $RUN_UID:$RUN_GID (the container may lack permission to change ownership on this mount, e.g. some bind mounts or non-root filesystems)"
			exit 1
		fi
	fi

	if [ "$RUN_UID" != "0" ]; then
		DB_DIR_UID=$(stat -c %u "$DB_DIR" 2>/dev/null || echo "")
		DB_DIR_GID=$(stat -c %g "$DB_DIR" 2>/dev/null || echo "")
		if [ "$DB_DIR_UID" != "$RUN_UID" ] || [ "$DB_DIR_GID" != "$RUN_GID" ]; then
			if ! chown "$RUN_UID:$RUN_GID" "$DB_DIR"; then
				log "error: failed to chown database directory $DB_DIR to $RUN_UID:$RUN_GID (the container may lack permission to change ownership on this mount, e.g. some bind mounts or non-root filesystems)"
				exit 1
			fi
		fi
	fi

	# Caddy writes an autosave config (and its own data dir) on every
	# config load even with `admin off` (#589). -P (never traverse
	# symlinks during the recursion) is GNU chown's default; passed
	# explicitly so that safety property doesn't silently depend on an
	# implicit default (see #590's discussion for why this matters and
	# why it's already safe).
	if ! mkdir -p /var/lib/caddy/data /var/lib/caddy/config; then
		log "error: failed to create /var/lib/caddy/{data,config}"
		exit 1
	fi
	if ! chown -RP "$RUN_UID:$RUN_GID" /var/lib/caddy; then
		log "error: failed to chown /var/lib/caddy to $RUN_UID:$RUN_GID"
		exit 1
	fi
}

if [ "$(id -u)" = "0" ]; then
	# ------------------------------------------------------------------
	# Root prelude. A stop signal arriving during this window (no flock,
	# no network I/O -- a handful of mkdir/stat/chown calls) is not a
	# failure: nothing has started yet, so there's nothing to drain, and
	# it's safe to just exit 0 immediately. Bash defers trap delivery
	# until the current foreground command returns, which bounds this
	# window to whichever single mkdir/chown/stat call happens to be in
	# flight.
	# ------------------------------------------------------------------
	trap 'log "termination requested during root setup, exiting cleanly"; exit 0' TERM INT

	# Determine UID/GID to run as: MAGPIE_UID/MAGPIE_GID env vars if set,
	# else detected from /data's existing ownership, else 1000:1000.
	# Mirrors entrypoint.sh's resolution (used by the two-container
	# image) -- duplicated, not shared, so that image's contract can't
	# be affected by this refactor.
	if [ -n "${MAGPIE_UID:-}" ]; then
		RUN_UID="$MAGPIE_UID"
	else
		RUN_UID=$(stat -c %u /data 2>/dev/null || echo 1000)
	fi
	if [ -n "${MAGPIE_GID:-}" ]; then
		RUN_GID="$MAGPIE_GID"
	else
		RUN_GID=$(stat -c %g /data 2>/dev/null || echo 1000)
	fi
	if ! [[ "$RUN_UID" =~ ^[0-9]+$ ]] || ! [[ "$RUN_GID" =~ ^[0-9]+$ ]]; then
		log "error: invalid RUN_UID=$RUN_UID or RUN_GID=$RUN_GID"
		exit 1
	fi

	# Publish for magpie-ctl-wrapper.sh (docker exec ... magpie-ctl ...):
	# nothing else in this script needs it post-drop anymore, since
	# uvicorn and caddy are both launched directly as this same uid
	# rather than individually gosu'd.
	mkdir -p /run
	echo "$RUN_UID:$RUN_GID" >/run/magpie-user
	chmod 644 /run/magpie-user

	magpie_resolve_storage_paths
	magpie_fixup_ownership

	# RUN_UID=0 (e.g. auto-detected from a fresh, still-root-owned
	# volume with no MAGPIE_UID/MAGPIE_GID set) means there's nothing to
	# drop to -- entrypoint.sh supports this same configuration ("no
	# privilege drop needed"). Falling through to `exec gosu 0:0 "$0"
	# "$@"` below would be a no-op privilege change that re-enters this
	# same `id -u = 0` branch again on the next line of execution,
	# looping forever -- skip the re-exec entirely instead and just
	# continue as root.
	if [ "$RUN_UID" = "0" ]; then
		log "RUN_UID is 0 -- continuing as root (no privilege drop configured)"
	else
		if ! command -v gosu >/dev/null 2>&1; then
			log "error: gosu is required to drop privileges but was not found in PATH"
			exit 1
		fi
		log "root setup complete, dropping to $RUN_UID:$RUN_GID"
		exec gosu "$RUN_UID:$RUN_GID" "$0" "$@"
	fi
fi

# ---------------------------------------------------------------------------
# Everything below runs as the non-root runtime uid -- via the exec above,
# or because the container was started non-root directly (e.g. `docker
# run --user`). uvicorn and caddy are launched directly, not through a
# per-child gosu: no capability related to privilege dropping (SETUID,
# SETGID) or to signaling a different-uid process (KILL) is needed past
# this point.
# ---------------------------------------------------------------------------

DB_INIT_PID=""
UVICORN_PID=""
CADDY_PID=""
SHUTTING_DOWN=0

# On SIGTERM/SIGINT (docker stop, or tini forwarding a signal sent to the
# container), forward SIGTERM to whichever of these is currently running
# so it drains (uvicorn, caddy) or just stops waiting (the DB-init flock,
# which has nothing to gracefully drain), and mark this as a requested
# shutdown so the final exit code is 0 rather than the fail-fast non-zero
# code.
# shellcheck disable=SC2317  # only reachable via the trap below
term_handler() {
	if [ "$SHUTTING_DOWN" -eq 0 ]; then
		SHUTTING_DOWN=1
		log "received termination signal, draining"
		[ -n "$DB_INIT_PID" ] && kill -TERM "$DB_INIT_PID" 2>/dev/null
		[ -n "$UVICORN_PID" ] && kill -TERM "$UVICORN_PID" 2>/dev/null
		[ -n "$CADDY_PID" ] && kill -TERM "$CADDY_PID" 2>/dev/null
	fi
}
trap term_handler TERM INT

# Re-resolve the same paths (cheap, no side effects if the root prelude
# already ran: STORAGE_DIR/DB_DIR already exist, so the mkdir branches
# are no-ops). If the root prelude was skipped entirely -- the container
# was started non-root directly -- this is the first and only
# resolution, and it's this uid's own responsibility to already own
# whatever it points at (nothing here can chown).
magpie_resolve_storage_paths

# Auto-initialize the database if it doesn't exist yet, guarded by the
# same flock entrypoint.sh used to use (concurrent container starts
# racing for /data). No gosu here: this whole process is already running
# as the target uid.
#
# Backgrounded (not run as a plain synchronous foreground command),
# specifically so that a signal arriving while blocked on `flock -x -w
# 30` is handled promptly: per bash's documented signal semantics, a
# trap does NOT fire until a synchronous foreground command completes,
# but explicitly `wait`ing on an already-backgrounded job IS
# signal-interruptible (the same property `wait -n` below relies on).
# Without this, a `docker stop` arriving during a lock contended by
# another container could block for up to the full 30s timeout before
# term_handler even runs.
(
	flock_status=0
	flock -x -w 30 200 || flock_status=$?
	if [ "$flock_status" -ne 0 ]; then
		if [ "$flock_status" -eq 1 ]; then
			log "error: failed to acquire database init lock on $DB_LOCK_FILE within 30 seconds (another process may be initializing the database)"
		else
			log "error: flock failed with exit code $flock_status while trying to lock $DB_LOCK_FILE (is flock available and working?)"
		fi
		exit 1
	fi

	if [ ! -f "$DB_PATH" ]; then
		log "database not found at $DB_PATH, running magpie-ctl init..."
		init_status=0
		/app/.venv/bin/python -m magpie.ctl init || init_status=$?
		if [ "$init_status" -ne 0 ]; then
			log "error: magpie-ctl init failed with exit code $init_status"
			if [ -f "$DB_PATH" ]; then
				log "removing incomplete database file at $DB_PATH"
				rm -f "$DB_PATH"
			fi
			exit 1
		fi
	fi
) 200>"$DB_LOCK_FILE" &
DB_INIT_PID=$!
# Same race as uvicorn's/caddy's below: catch up on a signal that arrived
# before DB_INIT_PID was captured.
if [ "$SHUTTING_DOWN" -eq 1 ]; then
	kill -TERM "$DB_INIT_PID" 2>/dev/null
fi
wait "$DB_INIT_PID"
DB_INIT_EXIT=$?
if [ "$SHUTTING_DOWN" -eq 1 ]; then
	log "graceful shutdown complete (during startup)"
	exit 0
fi
if [ "$DB_INIT_EXIT" -ne 0 ]; then
	log "database init failed (exit=$DB_INIT_EXIT), failing container"
	exit 1
fi

/app/.venv/bin/uvicorn magpie.server.app:app \
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
# Liveness is checked via /proc/$PID's existence rather than `kill -0`:
# equivalent as a liveness check (both see a zombie as "still there" until
# reaped), but doesn't depend on signal-permission semantics -- `kill -0`
# against a different-uid process needs CAP_KILL, which was a source of
# false-crash reports before the whole supervisor ran as one uid (#591);
# /proc's existence needs no capability at all, regardless of uid.
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
	if [ ! -d "/proc/$UVICORN_PID" ]; then
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

export XDG_DATA_HOME=/var/lib/caddy/data
export XDG_CONFIG_HOME=/var/lib/caddy/config
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
if [ -d "/proc/$UVICORN_PID" ]; then
	log "caddy exited (code=$FIRST_EXIT) - killing uvicorn, failing container"
	kill -TERM "$UVICORN_PID" 2>/dev/null
	wait "$UVICORN_PID" 2>/dev/null
elif [ -d "/proc/$CADDY_PID" ]; then
	log "uvicorn exited (code=$FIRST_EXIT) - killing caddy, failing container"
	kill -TERM "$CADDY_PID" 2>/dev/null
	wait "$CADDY_PID" 2>/dev/null
else
	log "both processes already gone (code=$FIRST_EXIT) - failing container"
fi

exit 1
