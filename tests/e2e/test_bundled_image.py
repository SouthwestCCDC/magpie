"""E2E tests for the bundled single-container image (Dockerfile.bundled, #586).

Regression coverage for docker/bundled/wrapper.sh's fail-fast /
graceful-shutdown classification: a `docker stop` must exit 0 whether it
arrives before or after uvicorn's startup-ordering gate completes, while a
process actually crashing (SIGKILL) must still fail the container (exit 1).
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Generator

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


def _run_container(image: str, data_dir: Path, name: str) -> None:
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
        name = "magpie-bundled-e2e-midstop"
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
        name = "magpie-bundled-e2e-killuvicorn"
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

                # Match the exact second argv element (the uvicorn script path),
                # not a substring search -- a substring search here would also
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
