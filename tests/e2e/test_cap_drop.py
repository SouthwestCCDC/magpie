"""E2E tests for entrypoint behavior under cap_drop: ALL security posture.

These tests verify that the container entrypoint works correctly when run with
production security hardening (cap_drop: ALL with minimal cap_add).

The required capabilities from PR #512 (fix/container-cap-drop-permission-denied):
  - CHOWN: Adjust ownership of /data/artifacts on first boot
  - DAC_OVERRIDE: Write to /data bind mount owned by non-root host user
  - SETUID: Required by gosu to switch user identity
  - SETGID: Required by gosu to switch group identity

Tests cover:
- Storage directory creation on first boot
- /run/magpie-user file creation (requires /run tmpfs)
- Privilege drop via gosu
- UID/GID detection and override
- Full init sequence with various UID/GID configurations
- Service health check (HTTP API responds after init)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import pytest

# Project root directory (where Dockerfile is located)
PROJECT_ROOT = Path(__file__).parent.parent.parent.absolute()

# Capabilities required for the entrypoint to function under cap_drop: ALL
CAP_DROP_ARGS = ["--cap-drop=ALL"]
CAP_ADD_ARGS = [
    "--cap-add=CHOWN",
    "--cap-add=DAC_OVERRIDE",
    "--cap-add=SETUID",
    "--cap-add=SETGID",
]


def _build_image(tag: str) -> None:
    """Build the magpie Docker image with the given tag.

    Args:
        tag: Docker image tag to apply.
    """
    subprocess.run(
        ["docker", "build", "-t", tag, "."],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )


def _remove_image(tag: str) -> None:
    """Remove a Docker image, ignoring errors.

    Args:
        tag: Docker image tag to remove.
    """
    subprocess.run(
        ["docker", "rmi", tag],
        capture_output=True,
    )


def _cleanup_docker_volume(path: str) -> None:
    """Make all files in a directory accessible so the host can delete them.

    When Docker containers run as a non-root UID and write files into a
    bind-mounted temp directory, the host runner may lack permission to remove
    those files (e.g. ``PermissionError: [Errno 1] Operation not permitted``).
    This helper runs a privileged alpine container to ``chmod -R 777`` the tree,
    making every entry deletable by the host user before ``shutil.rmtree``.

    Args:
        path: Absolute path on the host to the directory to fix up.
    """
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{path}:/cleanup",
            "alpine",
            "chmod",
            "-R",
            "777",
            "/cleanup",
        ],
        capture_output=True,
    )


def _extract_id_output(stdout: str) -> tuple[int, int]:
    """Extract UID and GID from 'id -u && id -g' command output.

    The entrypoint.sh may print diagnostic messages before our command output.
    This function finds the numeric lines that represent UID and GID.

    Args:
        stdout: Raw stdout from docker run command.

    Returns:
        Tuple of (uid, gid) extracted from output.

    Raises:
        ValueError: If UID/GID cannot be extracted from output.
    """
    lines = stdout.strip().split("\n")
    numeric_lines = [line.strip() for line in lines if line.strip().isdigit()]

    if len(numeric_lines) < 2:
        raise ValueError(
            f"Expected at least 2 numeric lines (UID and GID), got {len(numeric_lines)}: "
            f"{numeric_lines!r}\nFull stdout:\n{stdout}"
        )

    # Take the last two numeric lines (in case there are extra messages before)
    return int(numeric_lines[-2]), int(numeric_lines[-1])


def _wait_for_http_health(port: int, timeout: int = 60, interval: float = 1.0) -> bool:
    """Poll the /health endpoint until it returns 200 or timeout expires.

    Args:
        port: Host port the container is listening on.
        timeout: Maximum seconds to wait.
        interval: Seconds between checks.

    Returns:
        True if healthy, False if timed out.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"http://localhost:{port}/health", timeout=3.0)
            if r.status_code == 200:
                return True
        except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError):
            pass
        time.sleep(interval)
    return False


@pytest.mark.e2e
@pytest.mark.slow
class TestCapDropInit:
    """Tests for entrypoint init sequence under cap_drop: ALL."""

    @pytest.fixture(scope="class")
    def image_tag(self) -> str:  # type: ignore[override]
        """Build image once for all tests in this class, remove after."""
        tag = "magpie-cap-drop-test:latest"
        _build_image(tag)
        yield tag
        _remove_image(tag)

    def _run_cap_drop(
        self,
        image_tag: str,
        cmd: list[str],
        *,
        env: dict[str, str] | None = None,
        extra_args: list[str] | None = None,
        data_dir: str | None = None,
        port: int | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run the container with cap_drop: ALL and the required cap_add set.

        Args:
            image_tag: Docker image tag.
            cmd: Command to run inside the container.
            env: Extra environment variables to pass with -e.
            extra_args: Additional docker run arguments.
            data_dir: Host path to mount as /data.
            port: Host port to publish to container port 8000.
            check: Whether to raise on non-zero exit.

        Returns:
            CompletedProcess with stdout/stderr captured.
        """
        docker_cmd = ["docker", "run", "--rm"]
        docker_cmd.extend(CAP_DROP_ARGS)
        docker_cmd.extend(CAP_ADD_ARGS)
        # /run must be writable for /run/magpie-user (matches production tmpfs)
        docker_cmd.extend(["--tmpfs", "/run"])
        # /tmp must be writable for Python/FastAPI temp files (matches production tmpfs)
        docker_cmd.extend(["--tmpfs", "/tmp"])

        if data_dir:
            docker_cmd.extend(["-v", f"{data_dir}:/data"])

        if port:
            docker_cmd.extend(["-p", f"{port}:8000"])

        for key, value in (env or {}).items():
            docker_cmd.extend(["-e", f"{key}={value}"])

        if extra_args:
            docker_cmd.extend(extra_args)

        docker_cmd.append(image_tag)
        docker_cmd.extend(cmd)

        return subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            check=check,
        )

    def test_storage_dir_created_on_first_boot(self, image_tag: str) -> None:
        """Verify that STORAGE_DIR is created during first boot under cap_drop: ALL.

        The entrypoint must use CHOWN + DAC_OVERRIDE to create and own the
        /data/artifacts directory when it does not yet exist.
        """
        with tempfile.TemporaryDirectory(prefix="magpie_cap_init_") as temp_dir:
            temp_path = Path(temp_dir)
            # Do NOT pre-create artifacts/ — that is the first-boot scenario
            # Pre-create magpie.db so the flock/init step completes cleanly
            (temp_path / "magpie.db").touch()

            result = self._run_cap_drop(
                image_tag,
                ["sh", "-c", "test -d /data/artifacts && echo OK"],
                data_dir=str(temp_path),
            )

            assert "OK" in result.stdout, (
                "Expected /data/artifacts to be created by entrypoint.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
            assert (temp_path / "artifacts").is_dir(), (
                "/data/artifacts directory was not created on the host"
            )

    def test_magpie_user_file_created(self, image_tag: str) -> None:
        """Verify that /run/magpie-user is written during init under cap_drop: ALL."""
        with tempfile.TemporaryDirectory(prefix="magpie_cap_userfile_") as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "artifacts").mkdir()
            (temp_path / "magpie.db").touch()

            test_uid = 2500
            test_gid = 2501

            result = self._run_cap_drop(
                image_tag,
                ["sh", "-c", "cat /run/magpie-user"],
                data_dir=str(temp_path),
                env={"MAGPIE_UID": str(test_uid), "MAGPIE_GID": str(test_gid)},
            )

            lines = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
            # Find the "UID:GID" line
            file_content = next(
                (
                    line
                    for line in lines
                    if ":" in line and all(p.isdigit() for p in line.split(":"))
                ),
                None,
            )

            assert file_content == f"{test_uid}:{test_gid}", (
                f"Expected /run/magpie-user to contain '{test_uid}:{test_gid}', "
                f"got: {file_content!r}\nFull stdout:\n{result.stdout}"
            )

    def test_privilege_drop_with_explicit_uid_gid(self, image_tag: str) -> None:
        """Verify gosu drops to the requested UID/GID under cap_drop: ALL.

        SETUID + SETGID capabilities are required for gosu to work.
        """
        with tempfile.TemporaryDirectory(prefix="magpie_cap_drop_") as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "artifacts").mkdir()
            (temp_path / "magpie.db").touch()

            test_uid = 3300
            test_gid = 3301

            result = self._run_cap_drop(
                image_tag,
                ["sh", "-c", "id -u && id -g"],
                data_dir=str(temp_path),
                env={"MAGPIE_UID": str(test_uid), "MAGPIE_GID": str(test_gid)},
            )

            actual_uid, actual_gid = _extract_id_output(result.stdout)

            assert actual_uid == test_uid, (
                f"Expected UID {test_uid} after privilege drop, got {actual_uid}.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
            assert actual_gid == test_gid, (
                f"Expected GID {test_gid} after privilege drop, got {actual_gid}.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

    def test_privilege_drop_detects_volume_ownership(self, image_tag: str) -> None:
        """Verify UID/GID detection from /data ownership works under cap_drop: ALL."""
        with tempfile.TemporaryDirectory(prefix="magpie_cap_volown_") as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "artifacts").mkdir()
            (temp_path / "magpie.db").touch()

            stat_info = os.stat(temp_path)
            expected_uid = stat_info.st_uid
            expected_gid = stat_info.st_gid

            result = self._run_cap_drop(
                image_tag,
                ["sh", "-c", "id -u && id -g"],
                data_dir=str(temp_path),
            )

            actual_uid, actual_gid = _extract_id_output(result.stdout)

            assert actual_uid == expected_uid, (
                f"Container UID {actual_uid} does not match /data ownership {expected_uid}.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
            assert actual_gid == expected_gid, (
                f"Container GID {actual_gid} does not match /data ownership {expected_gid}.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

    def test_storage_dir_ownership_set_correctly(self, image_tag: str) -> None:
        """Verify that /data/artifacts is chowned to RUN_UID:RUN_GID during first boot.

        The CHOWN capability allows the entrypoint to transfer ownership of the
        newly-created artifacts directory to the target non-root user.
        """
        # Use mkdtemp + explicit cleanup because Docker creates /data/artifacts
        # as a non-runner UID; TemporaryDirectory.__exit__ cannot chmod those
        # files and raises PermissionError even with ignore_cleanup_errors=True.
        temp_dir = tempfile.mkdtemp(prefix="magpie_cap_chown_")
        try:
            temp_path = Path(temp_dir)
            # chmod 0755 so the container can traverse /data after gosu drops to
            # MAGPIE_UID (tempfile.mkdtemp creates with 0700 which blocks non-owner).
            os.chmod(temp_dir, 0o755)
            # Do NOT pre-create artifacts/ — let the entrypoint create and chown it
            (temp_path / "magpie.db").touch()

            test_uid = 4200
            test_gid = 4201

            result = self._run_cap_drop(
                image_tag,
                ["sh", "-c", "stat -c '%u %g' /data/artifacts"],
                data_dir=str(temp_path),
                env={"MAGPIE_UID": str(test_uid), "MAGPIE_GID": str(test_gid)},
            )

            # Parse "UID GID" from stat output (ignore entrypoint diagnostic lines)
            lines = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
            stat_line = next(
                (
                    line
                    for line in lines
                    if len(line.split()) == 2 and all(p.isdigit() for p in line.split())
                ),
                None,
            )

            assert stat_line is not None, (
                f"Could not parse 'UID GID' from stat output.\nFull stdout:\n{result.stdout}"
            )
            dir_uid, dir_gid = (int(x) for x in stat_line.split())
            assert dir_uid == test_uid, (
                f"Expected /data/artifacts UID {test_uid}, got {dir_uid}.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
            assert dir_gid == test_gid, (
                f"Expected /data/artifacts GID {test_gid}, got {dir_gid}.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
        finally:
            _cleanup_docker_volume(temp_dir)
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_fails_without_required_capabilities(self, image_tag: str) -> None:
        """Verify that the entrypoint fails when SETUID/SETGID are absent.

        Without SETUID+SETGID, gosu cannot switch identity.  The entrypoint
        should exit non-zero, not silently continue as root.
        """
        with tempfile.TemporaryDirectory(prefix="magpie_cap_fail_") as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "artifacts").mkdir()
            (temp_path / "magpie.db").touch()

            test_uid = 5000
            test_gid = 5001

            # Override cap_add to omit SETUID/SETGID — gosu must fail
            docker_cmd = [
                "docker",
                "run",
                "--rm",
                "--cap-drop=ALL",
                "--cap-add=CHOWN",
                "--cap-add=DAC_OVERRIDE",
                # SETUID and SETGID intentionally omitted
                "--tmpfs",
                "/run",
                "--tmpfs",
                "/tmp",
                "-v",
                f"{temp_path}:/data",
                "-e",
                f"MAGPIE_UID={test_uid}",
                "-e",
                f"MAGPIE_GID={test_gid}",
                image_tag,
                "sh",
                "-c",
                "id -u",
            ]

            result = subprocess.run(docker_cmd, capture_output=True, text=True)

            assert result.returncode != 0, (
                "Expected container to fail without SETUID/SETGID capabilities, "
                f"but it exited 0.\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )


@pytest.mark.e2e
@pytest.mark.slow
class TestCapDropServiceHealth:
    """Verify the full init + service start sequence under cap_drop: ALL.

    These tests start the FastAPI service and check that the /health endpoint
    responds, confirming that the entire init sequence (dir creation, lock,
    optional DB init, privilege drop, uvicorn start) succeeds under the
    production security posture.
    """

    @pytest.fixture(scope="class")
    def image_tag(self) -> str:  # type: ignore[override]
        """Build image once for all tests in this class."""
        tag = "magpie-cap-drop-health-test:latest"
        _build_image(tag)
        yield tag
        _remove_image(tag)

    def test_service_becomes_healthy_under_cap_drop(self, image_tag: str) -> None:
        """Full init + service boot under cap_drop: ALL with pre-existing data dir."""
        with tempfile.TemporaryDirectory(
            prefix="magpie_cap_health_", ignore_cleanup_errors=True
        ) as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "artifacts").mkdir()
            # Let the DB be created by magpie-ctl init inside the container

            # Pick an unprivileged port unlikely to be in use
            host_port = 18765

            docker_cmd = [
                "docker",
                "run",
                "--rm",
                "-d",
                "--name",
                f"magpie-cap-health-{os.getpid()}",
                *CAP_DROP_ARGS,
                *CAP_ADD_ARGS,
                "--tmpfs",
                "/run",
                "--tmpfs",
                "/tmp",
                "-v",
                f"{temp_path}:/data",
                "-p",
                f"{host_port}:8000",
                "-e",
                "MAGPIE_STORAGE_PATH=/data/artifacts",
                image_tag,
            ]

            container_id = None
            try:
                result = subprocess.run(docker_cmd, capture_output=True, text=True, check=True)
                container_id = result.stdout.strip()

                healthy = _wait_for_http_health(host_port, timeout=90)

                if not healthy:
                    logs = subprocess.run(
                        ["docker", "logs", container_id],
                        capture_output=True,
                        text=True,
                    )
                    pytest.fail(
                        "Service did not become healthy under cap_drop: ALL within 90s.\n"
                        f"Container logs:\n{logs.stdout}\n{logs.stderr}"
                    )

                # Confirm the response is the real health payload
                response = httpx.get(f"http://localhost:{host_port}/health", timeout=5.0)
                assert response.status_code == 200, (
                    f"Expected 200 from /health, got {response.status_code}: {response.text}"
                )

            finally:
                if container_id:
                    subprocess.run(
                        ["docker", "stop", container_id],
                        capture_output=True,
                    )

    def test_service_becomes_healthy_with_explicit_uid_gid(self, image_tag: str) -> None:
        """Full init sequence with MAGPIE_UID/GID set, under cap_drop: ALL."""
        # Use mkdtemp + explicit cleanup because Docker creates /data/artifacts
        # as a non-runner UID; TemporaryDirectory.__exit__ cannot chmod those
        # files and raises PermissionError even with ignore_cleanup_errors=True.
        temp_dir = tempfile.mkdtemp(prefix="magpie_cap_uid_health_")
        try:
            temp_path = Path(temp_dir)
            # chmod 0755 so the container can traverse /data after gosu drops to
            # MAGPIE_UID (tempfile.mkdtemp creates with 0700 which blocks non-owner).
            os.chmod(temp_dir, 0o755)
            # No artifacts dir or DB — full first-boot scenario

            host_port = 18766
            test_uid = 6000
            test_gid = 6001

            docker_cmd = [
                "docker",
                "run",
                "--rm",
                "-d",
                "--name",
                f"magpie-cap-uid-{os.getpid()}",
                *CAP_DROP_ARGS,
                *CAP_ADD_ARGS,
                "--tmpfs",
                "/run",
                "--tmpfs",
                "/tmp",
                "-v",
                f"{temp_path}:/data",
                "-p",
                f"{host_port}:8000",
                "-e",
                f"MAGPIE_UID={test_uid}",
                "-e",
                f"MAGPIE_GID={test_gid}",
                "-e",
                "MAGPIE_STORAGE_PATH=/data/artifacts",
                "-e",
                # DB must live inside artifacts/ because /data is owned by the CI
                # runner and MAGPIE_UID (6000) cannot write to /data directly.
                # The entrypoint chowns artifacts/ to MAGPIE_UID before init runs.
                "MAGPIE_DATABASE_PATH=/data/artifacts/magpie.db",
                image_tag,
            ]

            container_id = None
            try:
                result = subprocess.run(docker_cmd, capture_output=True, text=True, check=True)
                container_id = result.stdout.strip()

                healthy = _wait_for_http_health(host_port, timeout=90)

                if not healthy:
                    logs = subprocess.run(
                        ["docker", "logs", container_id],
                        capture_output=True,
                        text=True,
                    )
                    pytest.fail(
                        f"Service did not become healthy with UID={test_uid} GID={test_gid} "
                        f"under cap_drop: ALL.\n"
                        f"Container logs:\n{logs.stdout}\n{logs.stderr}"
                    )

                response = httpx.get(f"http://localhost:{host_port}/health", timeout=5.0)
                assert response.status_code == 200

            finally:
                if container_id:
                    subprocess.run(
                        ["docker", "stop", container_id],
                        capture_output=True,
                    )
        finally:
            _cleanup_docker_volume(temp_dir)
            shutil.rmtree(temp_dir, ignore_errors=True)
