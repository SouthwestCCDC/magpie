"""E2E tests for the bundled single-container image (Dockerfile.bundled, #586).

Regression coverage for docker/bundled/wrapper.sh's fail-fast /
graceful-shutdown classification: a `docker stop` must exit 0 whether it
arrives before or after uvicorn's startup-ordering gate completes, while a
process actually crashing (SIGKILL) must still fail the container (exit 1).

Also covers #589: Caddy runs non-root on :8080, and the container needs no
Linux capability beyond gosu-based privilege dropping (not even
CAP_NET_BIND_SERVICE).
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Generator

import httpx
import pytest

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
    """Poll `docker logs` until `substring` appears, or fail after `timeout`s."""
    for _ in range(timeout):
        result = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
        if substring in result.stdout + result.stderr:
            return
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
        relied on via a sleep: entrypoint.sh serializes first-boot DB init
        with `flock -x -w 30 200` on `{storage_dir}/.magpie-init.lock`. That
        file lives under the bind-mounted /data volume, so a host-held
        exclusive flock on the same path (same inode, shared through the
        bind mount) blocks entrypoint.sh before it ever execs uvicorn --
        uvicorn cannot answer /health while the host holds this lock, so
        wrapper.sh's startup gate is guaranteed to still be open when
        `docker stop` is issued below.
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
            # Matches entrypoint.sh's LOCK_DIR resolution for the no-
            # MAGPIE_STORAGE_PATH case: LOCK_DIR=/data/artifacts (already
            # exists, since we just created it), DB_LOCK_FILE=LOCK_DIR/.magpie-init.lock.
            lock_path = artifacts_dir / ".magpie-init.lock"
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                _run_container(bundled_image, data_dir, name)
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
                    "block entrypoint.sh's DB init as intended, so this test "
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
                        '&& basename "$p"; done',
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

                assert _exit_code(name) == 1, (
                    "a real crash (uvicorn killed after steady state) must still fail the container"
                )
            finally:
                _cleanup(name)


def _docker_top_user(name: str, comm: str) -> str | None:
    """Return the USER field docker top reports for a process matching `comm` exactly."""
    result = subprocess.run(
        ["docker", "top", name, "-o", "pid,user,comm"],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.strip().splitlines()[1:]:  # skip header row
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[2] == comm:
            return parts[1]
    return None


@pytest.mark.e2e
@pytest.mark.slow
class TestBundledImageNonRootCaddy:
    """Regression coverage for #589: Caddy must bind :8080 and run non-root,
    and the container must not need any Linux capability beyond what
    gosu-based privilege dropping already requires (in particular, not
    CAP_NET_BIND_SERVICE).
    """

    def test_caddy_runs_as_non_root_on_8080(self, bundled_image: str) -> None:
        """Caddy's process must be owned by the same non-root uid uvicorn
        runs as (not root), and it must be reachable on :8080.
        """
        name = f"magpie-bundled-e2e-nonroot-{uuid.uuid4().hex[:8]}"
        with tempfile.TemporaryDirectory(prefix="magpie_bundled_nonroot_") as tmp:
            _cleanup(name)
            try:
                _run_container(bundled_image, Path(tmp), name, extra_args=["-p", "0:8080"])
                _wait_for_log(name, "caddy started")

                caddy_user = _docker_top_user(name, "caddy")
                uvicorn_user = _docker_top_user(name, "uvicorn")
                assert caddy_user is not None, "could not find caddy in `docker top` output"
                assert uvicorn_user is not None, "could not find uvicorn in `docker top` output"
                assert caddy_user not in ("root", "0"), (
                    f"caddy must not run as root, got user={caddy_user!r}"
                )
                assert caddy_user == uvicorn_user, (
                    "caddy and uvicorn must run as the same uid (so caddy can read "
                    f"what uvicorn writes under /data), got caddy={caddy_user!r} "
                    f"uvicorn={uvicorn_user!r}"
                )

                # Port mapping proves Caddy is actually listening on :8080
                # inside the container (host port 0 -> Docker picks a free
                # ephemeral port).
                port_result = subprocess.run(
                    ["docker", "port", name, "8080/tcp"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                assert port_result.stdout.strip(), "container did not publish 8080/tcp"
            finally:
                _cleanup(name)

    def test_starts_and_serves_under_cap_drop_all(self, bundled_image: str) -> None:
        """The container must start healthy and serve real traffic with
        every Linux capability dropped except the small set gosu-based
        privilege dropping and first-boot ownership fixups require
        (SETUID/SETGID/CHOWN/DAC_OVERRIDE/FOWNER/KILL) -- notably, WITHOUT
        CAP_NET_BIND_SERVICE, proving Caddy's move to :8080 makes that
        capability unnecessary.
        """
        name = f"magpie-bundled-e2e-capdrop-{uuid.uuid4().hex[:8]}"
        init_name = f"{name}-init"
        with tempfile.TemporaryDirectory(prefix="magpie_bundled_capdrop_") as tmp:
            data_dir = Path(tmp)
            _cleanup(name)
            _cleanup(init_name)
            try:
                # First boot needs full default capabilities: entrypoint.sh
                # runs as root and chowns the fresh /data tree to the
                # runtime uid, which requires CAP_CHOWN/DAC_OVERRIDE/FOWNER
                # against a directory it doesn't yet own. This mirrors a
                # real deployment's first boot -- the cap-drop claim is
                # about steady-state operation, not bootstrapping an empty
                # volume.
                _run_container(bundled_image, data_dir, init_name)
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
                        "--cap-drop",
                        "ALL",
                        "--cap-add",
                        "SETUID",
                        "--cap-add",
                        "SETGID",
                        "--cap-add",
                        "CHOWN",
                        "--cap-add",
                        "DAC_OVERRIDE",
                        "--cap-add",
                        "FOWNER",
                        "--cap-add",
                        "KILL",
                    ],
                )
                _wait_for_log(name, "caddy started")

                for _ in range(30):
                    health = subprocess.run(
                        ["docker", "inspect", name, "--format", "{{.State.Health.Status}}"],
                        capture_output=True,
                        text=True,
                    ).stdout.strip()
                    if health == "healthy":
                        break
                    time.sleep(1)
                else:
                    pytest.fail(f"container never became healthy under cap-drop ALL: {health}")

                port = (
                    subprocess.run(
                        ["docker", "port", name, "8080/tcp"],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    .stdout.strip()
                    .split(":")[-1]
                )
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

                content = b"issue #589 cap-drop round-trip test\n"
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
