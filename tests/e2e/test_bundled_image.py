"""E2E tests for the bundled single-container image (Dockerfile.bundled, #586).

Regression coverage for docker/bundled/wrapper.sh's fail-fast /
graceful-shutdown classification: a `docker stop` must exit 0 whether it
arrives before or after uvicorn's startup-ordering gate completes, while a
process actually crashing (SIGKILL) must still fail the container (exit 1).
"""

from __future__ import annotations

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
        """
        name = "magpie-bundled-e2e-midstop"
        with tempfile.TemporaryDirectory(prefix="magpie_bundled_midstop_") as tmp:
            _cleanup(name)
            try:
                _run_container(bundled_image, Path(tmp), name)
                # Stop well inside the startup window: wrapper.sh's poll loop
                # allows up to 60s, and this repo's own smoke testing shows
                # steady-state (both processes up) isn't reached for several
                # seconds -- 0.3s is comfortably inside the startup gate.
                time.sleep(0.3)
                subprocess.run(["docker", "stop", name], check=True, capture_output=True)
                assert _exit_code(name) == 0, (
                    "docker stop during startup should exit 0 (graceful), "
                    "not report the container as failed"
                )
                # Exit code 0 alone isn't sufficient proof: on a fast/warm-cache
                # boot, uvicorn could become ready before the stop above lands,
                # in which case this test would exit the post-steady-state path
                # (also exit 0) without ever exercising the during-startup
                # branch it exists to guard. Assert wrapper.sh's
                # startup-specific log line to confirm the right branch ran.
                logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
                log_text = logs.stdout + logs.stderr
                assert "graceful shutdown complete (during startup)" in log_text, (
                    "expected wrapper.sh's during-startup shutdown log line -- "
                    "its absence means uvicorn became ready before docker stop "
                    "landed, so this test didn't actually exercise the "
                    "startup-window shutdown path it's meant to guard:\n"
                    f"{log_text}"
                )
            finally:
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
