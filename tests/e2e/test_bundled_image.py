"""E2E tests for the bundled single-container image (Dockerfile.bundled, #586).

Regression coverage for docker/bundled/wrapper.sh's fail-fast /
graceful-shutdown classification: a `docker stop` must exit 0 whether it
arrives before or after uvicorn's startup-ordering gate completes, while a
process actually crashing (SIGKILL) must still fail the container (exit 1).

Also covers #589 (Caddy binds :8080, non-root) and #591 (the whole
process tree -- tini itself, not just wrapper.sh/uvicorn/caddy -- runs
non-root in steady state; wrapper.sh's brief root prelude execs tini
dropped via gosu, so tini never runs root in front of a non-root child,
and CAP_KILL is no longer needed for tini's own signal forwarding).
"""

from __future__ import annotations

import fcntl
import os
import socket
import sqlite3
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Generator

import httpx
import pytest

from magpie.auth.database import init_database
from magpie.ctl.commands.migrate import CURRENT_DATA_FORMAT_VERSION

# Project root directory (where Dockerfile.bundled is located)
PROJECT_ROOT = Path(__file__).parent.parent.parent.absolute()
IMAGE_TAG = "magpie-bundled-e2e-test:latest"


@pytest.fixture(scope="module")
def bundled_image() -> Generator[str, None, None]:
    """Build the bundled image once for all tests in this module."""
    subprocess.run(
        ["docker", "build", "-f", "Dockerfile.bundled", "-t", IMAGE_TAG, "."],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )
    yield IMAGE_TAG
    subprocess.run(["docker", "rmi", IMAGE_TAG], capture_output=True)


def _own_uid_gid_args() -> list[str]:
    """`-e MAGPIE_UID=<ours>`/`-e MAGPIE_GID=<ours>` for the current process.

    Explicit (not relying on wrapper.sh's own auto-detection from /data's
    ownership) so tests asserting "runs as non-root" are deterministic
    regardless of what uid happens to own the test's tempdir in a given
    environment. Uses the *current* process's own uid/gid rather than a
    hardcoded constant like 1000: a hardcoded value only coincidentally
    matches the invoking user on some hosts, and on any host where it
    doesn't, the container chowns everything under the tempdir to that
    mismatched uid, orphaning it from pytest's own cleanup (a real CI
    failure this caused once already).

    Falls back to a fixed non-root uid/gid if pytest itself is running as
    root: passing MAGPIE_UID=0 through would make wrapper.sh legitimately
    keep RUN_UID=0 and stay root (its documented, correct fallback for
    that configuration) -- defeating the entire point of the tests that
    use this helper, which assert the image does NOT run as root. Safe
    to do without reintroducing the tempdir-cleanup issue above: root can
    always remove/chown files regardless of who owns them, so whatever
    uid the container chowns things to, pytest's own cleanup (also
    running as root in this branch) can still remove it.
    """
    uid, gid = os.getuid(), os.getgid()
    if uid == 0:
        uid, gid = 1000, 1000
    return ["-e", f"MAGPIE_UID={uid}", "-e", f"MAGPIE_GID={gid}"]


def _run_container(
    image: str, data_dir: Path, name: str, extra_args: list[str] | None = None
) -> None:
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "-v",
            f"{data_dir}:/data",
            "-e",
            "MAGPIE_ADMIN_TOKEN_SINK=discard",
            *(extra_args or []),
            image,
        ],
        check=True,
        capture_output=True,
    )


def _exit_code(name: str) -> int:
    result = subprocess.run(
        ["docker", "inspect", name, "--format", "{{.State.ExitCode}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


def _cleanup(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def _wait_for_log(name: str, substring: str, timeout: int = 30) -> None:
    """Poll `docker logs` until `substring` appears, or fail after `timeout`s.

    Fails fast (rather than spinning for the full timeout) if the container
    exits before the substring shows up, surfacing its exit code and logs
    so the failure is actionable.
    """
    for _ in range(timeout):
        result = subprocess.run(
            ["docker", "logs", name], check=True, capture_output=True, text=True
        )
        log_text = result.stdout + result.stderr
        if substring in log_text:
            return
        running = subprocess.run(
            ["docker", "inspect", name, "--format", "{{.State.Running}}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if running == "false":
            pytest.fail(
                f"{name} exited (code={_exit_code(name)}) before '{substring}' appeared "
                f"in its logs:\n{log_text}"
            )
        time.sleep(1)
    pytest.fail(f"'{substring}' did not appear in {name}'s logs within {timeout}s")


@pytest.mark.e2e
@pytest.mark.slow
class TestBundledImageShutdownClassification:
    """Regression tests for wrapper.sh's SHUTTING_DOWN vs. crash classification."""

    def test_docker_stop_during_startup_window_exits_zero(self, bundled_image: str) -> None:
        """A `docker stop` that arrives while uvicorn is still starting up must
        be treated as a graceful shutdown (exit 0), not a startup failure.

        Regression test for the bug where wrapper.sh's readiness-poll loop
        didn't check $SHUTTING_DOWN before exiting 1 on "uvicorn exited
        before becoming ready" -- a signal arriving during that window caused
        term_handler to kill uvicorn, which the poll loop then misreported
        as a crash.

        The startup window is forced open deterministically rather than
        relied on via a sleep: wrapper.sh (post-drop) serializes first-boot
        DB init with `flock -x -w 30 200` on `{storage_dir}/.magpie-init.lock`
        (#591 -- previously this lived in entrypoint.sh, which the bundled
        image no longer uses). That file lives under the bind-mounted
        /data volume, so a host-held exclusive flock on the same path
        (same inode, shared through the bind mount) blocks wrapper.sh
        before it ever starts uvicorn -- uvicorn cannot answer /health
        while the host holds this lock, so wrapper.sh's startup gate is
        guaranteed to still be open when `docker stop` is issued below.

        That guarantee only covers the DB-init phase, though -- it does
        nothing to slow down wrapper.sh's earlier PID-1 root prelude (id/
        stat/mkdir/chown x2/exec gosu+tini in wrapper.sh, none of which
        touch the lock file), which runs first and has its own separate
        SIGTERM trap ("termination requested during startup, exiting
        cleanly", not the "graceful shutdown complete (during startup)"
        this test asserts on). `docker run -d` returns once the container
        is merely created/started, not once wrapper.sh reaches any
        particular line, so without waiting for evidence the root prelude
        has actually finished, `docker stop` below races it -- normally
        losing that race by a wide margin, but wide enough to flake in CI
        when a `docker stop` lands during the root prelude instead of the
        (also exit-0, but differently-logged) DB-init window this test
        means to exercise. `_wait_for_log` below closes that race: it
        blocks until wrapper.sh's last root-prelude log line appears, so
        `docker stop` is guaranteed to land after the exec into
        gosu+tini, in the flock-held window.
        """
        # Unique suffix: hard-coded names could collide if tests ever run in
        # parallel (pytest-xdist, or multiple CI jobs sharing a Docker
        # daemon), with one run's `docker stop`/`docker rm` clobbering
        # another's container.
        name = f"magpie-bundled-e2e-midstop-{uuid.uuid4().hex[:8]}"
        with tempfile.TemporaryDirectory(prefix="magpie_bundled_midstop_") as tmp:
            _cleanup(name)
            data_dir = Path(tmp)
            artifacts_dir = data_dir / "artifacts"
            artifacts_dir.mkdir(parents=True)
            # Matches wrapper.sh's magpie_resolve_storage_paths LOCK_DIR
            # resolution for the no-MAGPIE_STORAGE_PATH case:
            # LOCK_DIR=/data/artifacts (already exists, since we just
            # created it), DB_LOCK_FILE=LOCK_DIR/.magpie-init.lock.
            lock_path = artifacts_dir / ".magpie-init.lock"
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                _run_container(bundled_image, data_dir, name)
                # Wait for wrapper.sh's root prelude to finish (its last
                # log line, immediately before it execs into gosu+tini)
                # before stopping: this is what makes the race above
                # deterministic. Once this line has been logged, the
                # container is provably past the un-covered PID-1 window
                # and about to (or already does) exec into the child that
                # will immediately block on the host-held flock, so
                # `docker stop` below is guaranteed to land in the
                # DB-init window this test is meant to exercise, not the
                # root prelude's own (differently-logged) shutdown path.
                _wait_for_log(name, "root setup complete, dropping to", timeout=10)
                subprocess.run(["docker", "stop", name], check=True, capture_output=True)
                assert _exit_code(name) == 0, (
                    "docker stop during startup should exit 0 (graceful), "
                    "not report the container as failed"
                )
                logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
                log_text = logs.stdout + logs.stderr
                assert "graceful shutdown complete (during startup)" in log_text, (
                    "expected wrapper.sh's during-startup shutdown log line -- "
                    "its absence means the host-held flock didn't actually "
                    "block wrapper.sh's DB init as intended, so this test "
                    "didn't exercise the startup-window shutdown path it's "
                    f"meant to guard:\n{log_text}"
                )
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
                _cleanup(name)

    def test_kill_uvicorn_after_ready_still_fails_fast(self, bundled_image: str) -> None:
        """A real crash (uvicorn killed after reaching steady state) must
        still fail the container (exit 1) -- guards against over-correcting
        the startup-window fix above into swallowing genuine crashes.
        """
        name = f"magpie-bundled-e2e-killuvicorn-{uuid.uuid4().hex[:8]}"
        with tempfile.TemporaryDirectory(prefix="magpie_bundled_killuvicorn_") as tmp:
            _cleanup(name)
            try:
                _run_container(bundled_image, Path(tmp), name)
                # Wait for steady state (both uvicorn and caddy up) before
                # killing, so this exercises the fail-fast path, not the
                # startup-window path covered by the test above.
                for _ in range(30):
                    result = subprocess.run(
                        ["docker", "logs", name],
                        capture_output=True,
                        text=True,
                    )
                    if "caddy started" in result.stdout + result.stderr:
                        break
                    time.sleep(1)
                else:
                    pytest.fail("container did not reach steady state (caddy never started)")

                # Match an exact argv field (the uvicorn script path) via
                # grep -x against each null-split /proc/*/cmdline field, not
                # a substring search -- a substring search here would also
                # match this very docker-exec command's own argv, since its
                # shell script text contains the same path literally.
                #
                # `/proc/[0-9]*` glob-expands in lexicographic (string)
                # order, not numeric order (e.g. "34" sorts before "7"), so
                # whichever PID happens to sort last is essentially
                # arbitrary and won't in general be uvicorn's -- trailing
                # `; true` keeps the overall exit code 0 regardless of
                # whether that last iteration's grep matched, since success
                # here is "did stdout capture a PID", not "did the last loop
                # iteration itself match".
                uvicorn_pid = subprocess.run(
                    [
                        "docker",
                        "exec",
                        name,
                        "bash",
                        "-c",
                        "for p in /proc/[0-9]*; do "
                        "tr '\\0' '\\n' < \"$p/cmdline\" 2>/dev/null "
                        "| grep -qx '/app/.venv/bin/uvicorn' "
                        '&& basename "$p"; done; true',
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
                assert uvicorn_pid, "could not locate uvicorn PID inside the container"

                # No standalone `kill` binary in the image (no procps) --
                # invoke bash's builtin instead.
                subprocess.run(
                    ["docker", "exec", name, "bash", "-c", f"kill -9 {uvicorn_pid}"],
                    check=True,
                    capture_output=True,
                )
                # Give the wrapper time to detect the exit and fail the container.
                for _ in range(10):
                    inspect = subprocess.run(
                        ["docker", "inspect", name, "--format", "{{.State.Running}}"],
                        capture_output=True,
                        text=True,
                    )
                    if inspect.stdout.strip() == "false":
                        break
                    time.sleep(1)

                crash_msg = (
                    "a real crash (uvicorn killed after steady state) must still fail the container"
                )
                assert _exit_code(name) == 1, crash_msg
            finally:
                _cleanup(name)


def _published_port(name: str, container_port: str = "8080/tcp") -> str:
    """Return the host port Docker published for `container_port`.

    Uses `docker inspect` (a single scalar field) rather than `docker port`
    (whose output can span multiple lines -- e.g. separate IPv4/IPv6
    bindings -- making naive text parsing flaky on IPv6-enabled hosts).
    """
    fmt = '{{(index (index .NetworkSettings.Ports "%s") 0).HostPort}}' % container_port
    result = subprocess.run(
        ["docker", "inspect", name, "--format", fmt],
        check=True,
        capture_output=True,
        text=True,
    )
    port = result.stdout.strip()
    assert port, f"{name} did not publish {container_port}"
    return port


def _docker_top_user(name: str, *comm_candidates: str) -> str | None:
    """Return the USER field docker top reports for a process matching any of
    `comm_candidates` exactly.

    Accepts multiple candidates because `docker top`'s COMMAND column for a
    shebang script (e.g. `/wrapper.sh`, `#!/bin/bash`) isn't guaranteed to
    show the script's own basename across Docker/kernel versions -- it can
    show the interpreter's instead (`bash`). There's only one such process
    in this container, so matching either name unambiguously identifies it.
    """
    result = subprocess.run(
        ["docker", "top", name, "-o", "pid,user,comm"],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.strip().splitlines()[1:]:  # skip header row
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[2] in comm_candidates:
            return parts[1]
    return None


@pytest.mark.e2e
@pytest.mark.slow
class TestBundledImageNonRootCaddy:
    """Regression coverage for #589 (Caddy binds :8080, non-root) and #591
    (the whole process tree -- tini itself, not just wrapper.sh and its
    children -- runs non-root in steady state). wrapper.sh (PID 1, the
    ENTRYPOINT) runs a brief root prelude, then execs tini dropped to the
    non-root runtime uid via gosu in that same exec, so tini itself ends
    up non-root rather than root in front of a non-root child; tini then
    forks wrapper.sh again, same uid, which runs the actual supervisor
    loop.
    """

    def test_supervisor_and_children_run_as_non_root(self, bundled_image: str) -> None:
        """In steady state, tini, wrapper.sh, uvicorn, and caddy must all
        be owned by the same non-root uid (not root), and Caddy must be
        reachable on :8080. This is a steady-state guarantee, not an
        instantaneous one: wrapper.sh briefly runs as root, as PID 1,
        before it execs gosu+tini -- that root prelude just isn't one of
        the long-lived processes this test (via `docker top`, taken well
        after startup) can observe.
        """
        name = f"magpie-bundled-e2e-nonroot-{uuid.uuid4().hex[:8]}"
        with tempfile.TemporaryDirectory(prefix="magpie_bundled_nonroot_") as tmp:
            _cleanup(name)
            try:
                _run_container(
                    bundled_image,
                    Path(tmp),
                    name,
                    extra_args=["-p", "0:8080", *_own_uid_gid_args()],
                )
                _wait_for_log(name, "caddy started")

                tini_user = _docker_top_user(name, "tini")
                wrapper_user = _docker_top_user(name, "wrapper.sh", "bash")
                uvicorn_user = _docker_top_user(name, "uvicorn")
                caddy_user = _docker_top_user(name, "caddy")
                assert tini_user is not None, "could not find tini in `docker top` output"
                assert wrapper_user is not None, "could not find wrapper.sh in `docker top` output"
                assert uvicorn_user is not None, "could not find uvicorn in `docker top` output"
                assert caddy_user is not None, "could not find caddy in `docker top` output"

                for proc_name, user in (
                    ("tini", tini_user),
                    ("wrapper.sh", wrapper_user),
                    ("uvicorn", uvicorn_user),
                    ("caddy", caddy_user),
                ):
                    root_msg = (
                        f"{proc_name} must not run as root in steady state, got user={user!r}"
                    )
                    assert user not in ("root", "0"), root_msg
                assert tini_user == wrapper_user == uvicorn_user == caddy_user, (
                    "tini, the supervisor, and both children must all run as the same uid "
                    f"(so caddy can read what uvicorn writes under /data, and tini's own "
                    f"signal forwarding needs no CAP_KILL), got tini={tini_user!r} "
                    f"wrapper.sh={wrapper_user!r} uvicorn={uvicorn_user!r} caddy={caddy_user!r}"
                )

                # A real request through the published port proves Caddy is
                # actually listening and routing on :8080 -- merely
                # checking that Docker published the port would only prove
                # the mapping exists, not that anything is answering on it.
                port = _published_port(name)
                health = httpx.get(f"http://127.0.0.1:{port}/health")
                assert health.status_code == 200
            finally:
                _cleanup(name)

    def test_starts_and_serves_under_minimal_capabilities(self, bundled_image: str) -> None:
        """The container must start healthy and serve real traffic in
        steady state (an already-provisioned /data, matching a real
        deployment's second and subsequent boots) with every Linux
        capability dropped except SETUID/SETGID/CHOWN -- notably WITHOUT
        CAP_NET_BIND_SERVICE (Caddy binds :8080, not :80, #589), KILL,
        DAC_OVERRIDE, and FOWNER.

        KILL is not needed: tini itself runs as the non-root runtime uid
        (wrapper.sh, PID 1 and root only briefly during its prelude,
        execs tini dropped via gosu in the same exec, so tini never runs
        root in front of a non-root child), so tini forwarding `docker
        stop`'s SIGTERM to its forked child is always a same-uid kill().
        DAC_OVERRIDE and FOWNER are NOT needed for this steady-state case
        either: the root prelude only touches /var/lib/caddy (baked into
        the image, root-owned) and dirs already owned by the target uid
        from the first boot below, and root chowning something it
        already owns needs neither. (DAC_OVERRIDE IS still needed on a
        genuinely fresh boot against a bind mount not already owned by
        root or the target uid -- root creating new files/dirs inside a
        directory it doesn't own needs it. That's the first-boot step
        below, deliberately run with full default capabilities, matching
        a real deployment's first boot.)
        """
        name = f"magpie-bundled-e2e-capdrop-{uuid.uuid4().hex[:8]}"
        init_name = f"{name}-init"
        with tempfile.TemporaryDirectory(prefix="magpie_bundled_capdrop_") as tmp:
            data_dir = Path(tmp)
            # tempfile.TemporaryDirectory() creates its directory mode
            # 0700 (owner-only, no group/other access at all) -- more
            # restrictive than a realistic bind-mount host directory,
            # which is normally at least 0755. At 0700, root can't even
            # traverse into it without DAC_OVERRIDE, which would make
            # every restart need DAC_OVERRIDE regardless of whether
            # anything inside actually needs creating -- an artifact of
            # this test's tempdir, not of steady-state operation on a
            # normal host directory. Loosen it to model that realistic
            # case.
            data_dir.chmod(0o755)
            _cleanup(name)
            _cleanup(init_name)
            try:
                # MAGPIE_UID/MAGPIE_GID explicitly set to the *current*
                # process's own uid/gid (see _own_uid_gid_args) -- both
                # for deterministic behavior (not dependent on whatever
                # uid happens to own the tempdir in a given environment)
                # and so TemporaryDirectory's own cleanup can still remove
                # what the containers chowned under it afterwards.
                #
                # First boot: full default capabilities, provisioning the
                # bind-mounted /data (see docstring -- this step needs
                # DAC_OVERRIDE, which the restart below deliberately omits).
                _run_container(bundled_image, data_dir, init_name, extra_args=_own_uid_gid_args())
                _wait_for_log(init_name, "caddy started")
                subprocess.run(["docker", "stop", init_name], check=True, capture_output=True)
                _cleanup(init_name)

                _run_container(
                    bundled_image,
                    data_dir,
                    name,
                    extra_args=[
                        "-p",
                        "0:8080",
                        *_own_uid_gid_args(),
                        "--cap-drop",
                        "ALL",
                        "--cap-add",
                        "SETUID",
                        "--cap-add",
                        "SETGID",
                        "--cap-add",
                        "CHOWN",
                    ],
                )
                _wait_for_log(name, "caddy started")

                for _ in range(30):
                    health = subprocess.run(
                        ["docker", "inspect", name, "--format", "{{.State.Health.Status}}"],
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout.strip()
                    if health == "healthy":
                        break
                    time.sleep(1)
                else:
                    pytest.fail(
                        f"container never became healthy under minimal capabilities: {health}"
                    )

                port = _published_port(name)
                base_url = f"http://127.0.0.1:{port}"

                unauth = httpx.get(f"{base_url}/api/v1/artifacts")
                assert unauth.status_code == 401

                token_result = subprocess.run(
                    [
                        "docker",
                        "exec",
                        name,
                        "magpie-ctl",
                        "token",
                        "create",
                        "--name",
                        "e2e-capdrop",
                        "--scope",
                        "admin",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
                token = next(
                    line.strip() for line in token_result.splitlines() if line.startswith("mgp_")
                )
                headers = {"Authorization": f"Bearer {token}"}

                content = b"issue #591 minimal-capability round-trip test\n"
                push = httpx.post(
                    f"{base_url}/api/v1/upload/e2e-capdrop-artifact",
                    headers=headers,
                    files={"file": ("artifact.txt", content)},
                )
                assert push.status_code == 200, push.text
                download_url = base_url + push.json()["download_url"]

                get_protected = httpx.get(download_url, headers=headers)
                assert get_protected.status_code == 200
                assert get_protected.content == content

                get_protected_noauth = httpx.get(download_url)
                assert get_protected_noauth.status_code == 401

                push_public = httpx.post(
                    f"{base_url}/api/v1/upload/public/e2e-capdrop-public",
                    headers=headers,
                    files={"file": ("artifact.txt", content)},
                )
                assert push_public.status_code == 200, push_public.text
                public_url = base_url + push_public.json()["download_url"]

                get_public_noauth = httpx.get(public_url)
                assert get_public_noauth.status_code == 200
                assert get_public_noauth.content == content
            finally:
                _cleanup(name)
                _cleanup(init_name)


def _runtime_ids() -> tuple[int, int]:
    """Non-root uid/gid to start containers as directly (`--user`)."""
    uid, gid = os.getuid(), os.getgid()
    return (1000, 1000) if uid == 0 else (uid, gid)


def _wait_healthy(name: str, timeout: int = 90) -> str:
    """Wait for Caddy's /health on the published port; return the port."""
    _wait_for_log(name, "caddy started", timeout=timeout)
    port = _published_port(name)
    deadline = time.monotonic() + 30
    while True:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                return port
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            pytest.fail(f"{name} never answered /health:\n{_logs(name)}")
        time.sleep(1)


def _process_uids(name: str) -> set[int]:
    top = subprocess.run(
        ["docker", "top", name, "-o", "pid,uid,comm"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return {int(line.split(None, 2)[1]) for line in top.strip().splitlines()[1:]}


@pytest.mark.e2e
@pytest.mark.slow
class TestBundledImageStartedNonRoot:
    """Started directly as the runtime uid (Kubernetes runAsNonRoot,
    `docker run --user`), with no root prelude: Caddy's state falls back
    to TMPDIR, and the restricted Kubernetes profile (no capabilities,
    no-new-privileges, read-only root filesystem with only /tmp writable)
    must serve authenticated traffic.
    """

    def _start(self, image: str, data_dir: Path, name: str, extra: list[str]) -> str:
        uid, gid = _runtime_ids()
        if os.getuid() == 0:
            os.chown(data_dir, uid, gid)
        _run_container(
            image,
            data_dir,
            name,
            extra_args=["-p", "0:8080", "--user", f"{uid}:{gid}", *extra],
        )
        return _wait_healthy(name)

    def test_user_flag_starts_and_runs_as_that_uid(self, bundled_image: str) -> None:
        name = f"magpie-bundled-e2e-user-{uuid.uuid4().hex[:8]}"
        with tempfile.TemporaryDirectory(prefix="magpie_bundled_user_") as tmp:
            _cleanup(name)
            try:
                self._start(bundled_image, Path(tmp), name, [])
                assert _process_uids(name) == {_runtime_ids()[0]}
                assert "keeping Caddy's state in" in _logs(name)
            finally:
                _cleanup(name)

    def test_restricted_kubernetes_profile_serves(self, bundled_image: str) -> None:
        name = f"magpie-bundled-e2e-restricted-{uuid.uuid4().hex[:8]}"
        with tempfile.TemporaryDirectory(prefix="magpie_bundled_restricted_") as tmp:
            data_dir = Path(tmp)
            _cleanup(name)
            try:
                port = self._start(
                    bundled_image,
                    data_dir,
                    name,
                    [
                        "--read-only",
                        "--tmpfs",
                        "/tmp",
                        "--cap-drop",
                        "ALL",
                        "--security-opt",
                        "no-new-privileges",
                        "-e",
                        "MAGPIE_ADMIN_TOKEN_SINK=file",
                    ],
                )
                token = (data_dir / "admin-token").read_text().strip()
                base = f"http://127.0.0.1:{port}"
                assert httpx.get(f"{base}/api/v1/status").status_code == 401
                status = httpx.get(
                    f"{base}/api/v1/status", headers={"Authorization": f"Bearer {token}"}
                )
                assert status.status_code == 200, status.text
                assert _process_uids(name) == {_runtime_ids()[0]}
            finally:
                _cleanup(name)

    def test_root_start_on_read_only_rootfs_without_caddy_tmpfs(self, bundled_image: str) -> None:
        """Root prelude can't provision /var/lib/caddy on a read-only root
        filesystem; it must warn and fall back rather than fail."""
        name = f"magpie-bundled-e2e-rootro-{uuid.uuid4().hex[:8]}"
        with tempfile.TemporaryDirectory(prefix="magpie_bundled_rootro_") as tmp:
            _cleanup(name)
            try:
                _run_container(
                    bundled_image,
                    Path(tmp),
                    name,
                    extra_args=[
                        "-p",
                        "0:8080",
                        "--read-only",
                        "--tmpfs",
                        "/run",
                        "--tmpfs",
                        "/tmp",
                        *_own_uid_gid_args(),
                    ],
                )
                _wait_healthy(name)
                assert "could not provision /var/lib/caddy" in _logs(name)
                assert 0 not in _process_uids(name)
            finally:
                _cleanup(name)


def _host_db(data_dir: Path, user_version: int | None) -> Path:
    """Create `data_dir/magpie.db` from the host, optionally stamped.

    ``user_version=0`` models a 0.1.x database (same schema, never stamped).
    """
    db_path = data_dir / "magpie.db"
    init_database(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tokens (name, token_hash, scope, enabled, created_at) "
            "VALUES ('pre-existing', 'hash-pre-existing', 'read', 1, '2026-01-01T00:00:00')"
        )
        if user_version is not None:
            conn.execute(f"PRAGMA user_version = {int(user_version)}")
        conn.commit()
    finally:
        conn.close()
    return db_path


def _host_db_version(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _host_token_names(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return sorted(row[0] for row in conn.execute("SELECT name FROM tokens"))
    finally:
        conn.close()


def _logs(name: str) -> str:
    result = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
    return result.stdout + result.stderr


def _wait_for_exit(name: str, timeout: int = 60) -> int:
    """Wait for the container to stop on its own and return its exit code."""
    for _ in range(timeout):
        running = subprocess.run(
            ["docker", "inspect", name, "--format", "{{.State.Running}}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if running == "false":
            return _exit_code(name)
        time.sleep(1)
    pytest.fail(f"{name} was still running after {timeout}s; expected it to refuse to start")


@pytest.mark.e2e
@pytest.mark.slow
class TestBundledImageStartupMigration:
    """Issue #635: the container brings the database to the current data format
    on every start (init on a fresh volume, then migrate), under the init lock,
    before uvicorn or Caddy start -- and refuses to start otherwise.
    """

    def test_fresh_volume_is_initialized_and_stamped_current(self, bundled_image: str) -> None:
        name = f"magpie-bundled-e2e-mig-fresh-{uuid.uuid4().hex[:8]}"
        with tempfile.TemporaryDirectory(prefix="magpie_bundled_mig_fresh_") as tmp:
            data_dir = Path(tmp)
            _cleanup(name)
            try:
                _run_container(bundled_image, data_dir, name, extra_args=_own_uid_gid_args())
                _wait_for_log(name, "caddy started")
                subprocess.run(["docker", "stop", name], check=True, capture_output=True)
                log_text = _logs(name)
                assert "running magpie-ctl init" in log_text, log_text
                assert f"v0 -> v{CURRENT_DATA_FORMAT_VERSION}" in log_text, log_text
                assert _host_db_version(data_dir / "magpie.db") == CURRENT_DATA_FORMAT_VERSION
            finally:
                _cleanup(name)

    def test_unstamped_0_1_x_database_is_migrated_on_start(self, bundled_image: str) -> None:
        name = f"magpie-bundled-e2e-mig-legacy-{uuid.uuid4().hex[:8]}"
        with tempfile.TemporaryDirectory(prefix="magpie_bundled_mig_legacy_") as tmp:
            data_dir = Path(tmp)
            db_path = _host_db(data_dir, user_version=0)
            _cleanup(name)
            try:
                _run_container(bundled_image, data_dir, name, extra_args=_own_uid_gid_args())
                _wait_for_log(name, "caddy started")
                subprocess.run(["docker", "stop", name], check=True, capture_output=True)
                log_text = _logs(name)
                assert f"Migrated data format: v0 -> v{CURRENT_DATA_FORMAT_VERSION}" in log_text
                assert "running magpie-ctl init" not in log_text, log_text
                # Migration happens before uvicorn starts serving.
                assert log_text.index("Migrated data format") < log_text.index("uvicorn started")
                assert _host_db_version(db_path) == CURRENT_DATA_FORMAT_VERSION
                assert _host_token_names(db_path) == ["pre-existing"]
            finally:
                _cleanup(name)

    def test_restart_at_current_version_is_a_quiet_no_op(self, bundled_image: str) -> None:
        name = f"magpie-bundled-e2e-mig-noop-{uuid.uuid4().hex[:8]}"
        with tempfile.TemporaryDirectory(prefix="magpie_bundled_mig_noop_") as tmp:
            data_dir = Path(tmp)
            db_path = _host_db(data_dir, user_version=CURRENT_DATA_FORMAT_VERSION)
            _cleanup(name)
            try:
                _run_container(bundled_image, data_dir, name, extra_args=_own_uid_gid_args())
                _wait_for_log(name, "caddy started")
                subprocess.run(["docker", "stop", name], check=True, capture_output=True)
                assert _exit_code(name) == 0
                log_text = _logs(name)
                for unexpected in ("Migrated", "already current", "magpie-ctl init", "error"):
                    assert unexpected not in log_text, f"{unexpected!r} in logs:\n{log_text}"
                assert _host_db_version(db_path) == CURRENT_DATA_FORMAT_VERSION
                assert _host_token_names(db_path) == ["pre-existing"]
            finally:
                _cleanup(name)

    def test_newer_than_binary_database_refuses_to_start_untouched(
        self, bundled_image: str
    ) -> None:
        name = f"magpie-bundled-e2e-mig-newer-{uuid.uuid4().hex[:8]}"
        newer = CURRENT_DATA_FORMAT_VERSION + 1
        with tempfile.TemporaryDirectory(prefix="magpie_bundled_mig_newer_") as tmp:
            data_dir = Path(tmp)
            db_path = _host_db(data_dir, user_version=newer)
            before = db_path.read_bytes()
            _cleanup(name)
            try:
                _run_container(bundled_image, data_dir, name, extra_args=_own_uid_gid_args())
                assert _wait_for_exit(name) != 0
                log_text = _logs(name)
                refusal = next(
                    (line for line in log_text.splitlines() if "is newer than this build" in line),
                    None,
                )
                assert refusal is not None, log_text
                assert f"({newer})" in refusal
                assert f"current: {CURRENT_DATA_FORMAT_VERSION}" in refusal
                assert "newer" in refusal and "restore a backup" in refusal
                assert f"magpie-ctl migrate --to {CURRENT_DATA_FORMAT_VERSION}" in refusal
                assert "refusing to start" in log_text
                assert "uvicorn started" not in log_text
                assert "caddy started" not in log_text
                assert db_path.read_bytes() == before
                assert _host_db_version(db_path) == newer
            finally:
                _cleanup(name)

    def test_migrate_to_reverts_via_docker_exec_and_restart_migrates_forward(
        self, bundled_image: str
    ) -> None:
        """Issue #646: the documented downgrade step works in a running container,
        as the runtime user, and the next start of the same image migrates forward
        again (startup never stays below its own format).
        """
        name = f"magpie-bundled-e2e-mig-revert-{uuid.uuid4().hex[:8]}"
        with tempfile.TemporaryDirectory(prefix="magpie_bundled_mig_revert_") as tmp:
            data_dir = Path(tmp)
            db_path = _host_db(data_dir, user_version=0)
            _cleanup(name)
            try:
                _run_container(bundled_image, data_dir, name, extra_args=_own_uid_gid_args())
                _wait_for_log(name, "caddy started")
                assert _host_db_version(db_path) == CURRENT_DATA_FORMAT_VERSION

                revert = subprocess.run(
                    ["docker", "exec", name, "magpie-ctl", "migrate", "--to", "0"],
                    capture_output=True,
                    text=True,
                )
                assert revert.returncode == 0, revert.stdout + revert.stderr
                assert f"v{CURRENT_DATA_FORMAT_VERSION} -> v0" in revert.stdout
                assert _host_db_version(db_path) == 0
                assert _host_token_names(db_path) == ["pre-existing"]
                # Written as the runtime user, not root (see _own_uid_gid_args()).
                run_uid = os.getuid() or 1000
                owners = {p.stat().st_uid for p in data_dir.iterdir()}
                assert owners == {run_uid}, owners

                subprocess.run(["docker", "stop", name], check=True, capture_output=True)
                subprocess.run(["docker", "start", name], check=True, capture_output=True)
                for _ in range(30):
                    if _logs(name).count("caddy started") >= 2:
                        break
                    time.sleep(1)
                log_text = _logs(name)
                assert log_text.count("caddy started") >= 2, log_text
                assert log_text.count("Migrated data format: v0 ->") == 2, log_text
                assert _host_db_version(db_path) == CURRENT_DATA_FORMAT_VERSION
            finally:
                _cleanup(name)

    def test_migration_failure_exits_before_serving(self, bundled_image: str) -> None:
        """An unreadable database fails migrate, which fails the container."""
        name = f"magpie-bundled-e2e-mig-fail-{uuid.uuid4().hex[:8]}"
        with tempfile.TemporaryDirectory(prefix="magpie_bundled_mig_fail_") as tmp:
            data_dir = Path(tmp)
            db_path = data_dir / "magpie.db"
            garbage = b"not a sqlite database" * 200
            db_path.write_bytes(garbage)
            _cleanup(name)
            try:
                _run_container(bundled_image, data_dir, name, extra_args=_own_uid_gid_args())
                assert _wait_for_exit(name) != 0
                log_text = _logs(name)
                assert "magpie-ctl migrate failed" in log_text, log_text
                assert "uvicorn started" not in log_text
                assert db_path.read_bytes() == garbage
            finally:
                _cleanup(name)

    def test_concurrent_starts_on_one_fresh_volume_serialize(self, bundled_image: str) -> None:
        """Two containers released onto the init lock at the same moment: exactly
        one initializes and migrates, the other finds nothing to do, both serve.

        The host holds the init lock until both containers are past their root
        prelude and blocked on it, so the race is forced rather than hoped for.
        """
        names = [f"magpie-bundled-e2e-mig-race{i}-{uuid.uuid4().hex[:8]}" for i in (1, 2)]
        with tempfile.TemporaryDirectory(prefix="magpie_bundled_mig_race_") as tmp:
            data_dir = Path(tmp)
            artifacts_dir = data_dir / "artifacts"
            artifacts_dir.mkdir(parents=True)
            lock_fd = os.open(artifacts_dir / ".magpie-init.lock", os.O_CREAT | os.O_RDWR)
            for name in names:
                _cleanup(name)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                for name in names:
                    _run_container(bundled_image, data_dir, name, extra_args=_own_uid_gid_args())
                for name in names:
                    _wait_for_log(name, "root setup complete, dropping to", timeout=10)
                time.sleep(1)
                fcntl.flock(lock_fd, fcntl.LOCK_UN)

                for name in names:
                    _wait_for_log(name, "caddy started")
                logs = [_logs(name) for name in names]
                assert sum("running magpie-ctl init" in text for text in logs) == 1, logs
                assert sum("Migrated data format" in text for text in logs) == 1, logs
                assert not any("error" in text for text in logs), logs
                for name in names:
                    subprocess.run(["docker", "stop", name], check=True, capture_output=True)
                    assert _exit_code(name) == 0
                assert _host_db_version(data_dir / "magpie.db") == CURRENT_DATA_FORMAT_VERSION
            finally:
                os.close(lock_fd)
                for name in names:
                    _cleanup(name)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _set_env_keys(text: str, values: dict[str, str]) -> str:
    """Replace (or append) KEY=value lines in a .env body."""
    kept = [
        line
        for line in text.splitlines()
        if line.split("=", 1)[0] not in values or line.lstrip().startswith("#")
    ]
    return "\n".join([*kept, *(f"{k}={v}" for k, v in values.items())]) + "\n"


@pytest.mark.e2e
@pytest.mark.slow
class TestReleaseAssetsPlainCompose:
    """The documented no-installer deploy (docs/releases.md "Release Assets").

    Rendered release assets plus `cp env.example .env` on a data directory
    Docker itself creates (root-owned) must not run anything as root.
    """

    def test_fresh_deploy_runs_as_env_example_uid(self, bundled_image: str, tmp_path: Path):
        assets = tmp_path / "assets"
        assets.mkdir()
        subprocess.run(  # noqa: S603
            [
                "bash",
                str(PROJECT_ROOT / "scripts" / "render_release_assets.sh"),
                "0.0.0",
                "sha256:" + "0" * 64,
                str(assets),
            ],
            check=True,
            capture_output=True,
        )
        env_example = (assets / "env.example").read_text()
        ids = dict(
            line.split("=", 1)
            for line in env_example.splitlines()
            if line.startswith(("MAGPIE_UID=", "MAGPIE_GID="))
        )
        uid, gid = int(ids["MAGPIE_UID"]), int(ids["MAGPIE_GID"])
        assert uid != 0 and gid != 0, ids

        data_dir = tmp_path / "data"  # absent: Docker creates it, as root
        port = _free_port()
        (assets / ".env").write_text(
            _set_env_keys(
                env_example,
                {
                    "MAGPIE_ADMIN_TOKEN_SINK": "discard",
                    "MAGPIE_IMAGE": bundled_image,
                    "MAGPIE_DATA_DIR": str(data_dir),
                    "MAGPIE_HTTP_PORT": str(port),
                    "MAGPIE_BIND_IP": "127.0.0.1",
                },
            )
        )
        compose = [
            "docker",
            "compose",
            "-p",
            f"magpie-assets-e2e-{uuid.uuid4().hex[:8]}",
            "-f",
            str(assets / "docker-compose.yml"),
            "--env-file",
            str(assets / ".env"),
        ]
        try:
            subprocess.run([*compose, "up", "-d"], check=True, capture_output=True)  # noqa: S603
            deadline = time.monotonic() + 90
            while True:
                try:
                    if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    logs = subprocess.run(  # noqa: S603
                        [*compose, "logs"], capture_output=True, text=True
                    )
                    pytest.fail(f"stack never became healthy:\n{logs.stdout}{logs.stderr}")
                time.sleep(2)

            cid = subprocess.run(  # noqa: S603
                [*compose, "ps", "-q", "magpie"], check=True, capture_output=True, text=True
            ).stdout.strip()
            top = subprocess.run(  # noqa: S603
                ["docker", "top", cid, "-o", "pid,uid,comm"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            rows = [line.split(None, 2) for line in top.strip().splitlines()[1:]]
            assert rows, top
            assert all(int(row[1]) == uid for row in rows), top

            owners = subprocess.run(  # noqa: S603
                ["docker", "exec", cid, "stat", "-c", "%u:%g %n", "/data", "/data/magpie.db"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.split("\n")
            for line in filter(None, owners):
                assert line.split()[0] == f"{uid}:{gid}", owners
        finally:
            subprocess.run([*compose, "down", "-v"], capture_output=True)  # noqa: S603
            # Hand the tree back so pytest's tmp_path cleanup can remove it.
            subprocess.run(  # noqa: S603
                [
                    "docker",
                    "run",
                    "--rm",
                    "--entrypoint",
                    "chown",
                    "-v",
                    f"{tmp_path}:/t",
                    bundled_image,
                    "-R",
                    f"{os.getuid()}:{os.getgid()}",
                    "/t",
                ],
                capture_output=True,
            )
