"""E2E tests for privilege-dropping functionality in entrypoint.sh.

Tests verify that the container correctly:
- Detects UID/GID from mounted volumes
- Respects MAGPIE_UID/MAGPIE_GID environment variables
- Validates UID/GID are positive integers
- Runs processes as the correct user
- Creates /run/magpie-user file with correct permissions
- Handles root (UID=0) case correctly
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

# Project root directory (where docker-compose.yml is located)
PROJECT_ROOT = Path(__file__).parent.parent.parent.absolute()


@pytest.mark.e2e
@pytest.mark.slow
class TestPrivilegeDropping:
    """Tests for UID/GID detection and privilege dropping."""

    def test_container_detects_volume_ownership(self) -> None:
        """Test that container detects UID/GID from /data/artifacts ownership."""
        # Create a temporary directory with known ownership
        with tempfile.TemporaryDirectory(prefix="magpie_privtest_") as temp_dir:
            temp_path = Path(temp_dir)
            artifacts_dir = temp_path / "artifacts"
            artifacts_dir.mkdir(parents=True)

            # Get current user's UID/GID
            stat_info = os.stat(temp_path)
            expected_uid = stat_info.st_uid
            expected_gid = stat_info.st_gid

            # Build the image
            subprocess.run(
                ["docker", "build", "-t", "magpie-privtest:latest", "."],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
            )

            try:
                # Run container with volume mount and check the process UID
                result = subprocess.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "-v",
                        f"{artifacts_dir}:/data/artifacts",
                        "magpie-privtest:latest",
                        "sh",
                        "-c",
                        "id -u && id -g",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )

                # Parse output (first line is UID, second is GID)
                lines = result.stdout.strip().split("\n")
                actual_uid = int(lines[0])
                actual_gid = int(lines[1])

                assert actual_uid == expected_uid, (
                    f"Container UID {actual_uid} does not match volume ownership {expected_uid}"
                )
                assert actual_gid == expected_gid, (
                    f"Container GID {actual_gid} does not match volume ownership {expected_gid}"
                )

            finally:
                # Cleanup: remove test image
                subprocess.run(
                    ["docker", "rmi", "magpie-privtest:latest"],
                    capture_output=True,
                )

    def test_container_respects_magpie_uid_gid_env(self) -> None:
        """Test that MAGPIE_UID/MAGPIE_GID environment variables override detection."""
        # Build the image
        subprocess.run(
            ["docker", "build", "-t", "magpie-privtest-env:latest", "."],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
        )

        try:
            # Run container with explicit UID/GID environment variables
            test_uid = 5000
            test_gid = 5001

            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-e",
                    f"MAGPIE_UID={test_uid}",
                    "-e",
                    f"MAGPIE_GID={test_gid}",
                    "magpie-privtest-env:latest",
                    "sh",
                    "-c",
                    "id -u && id -g",
                ],
                capture_output=True,
                text=True,
                check=True,
            )

            # Parse output
            lines = result.stdout.strip().split("\n")
            actual_uid = int(lines[0])
            actual_gid = int(lines[1])

            assert actual_uid == test_uid, (
                f"Container UID {actual_uid} does not match MAGPIE_UID {test_uid}"
            )
            assert actual_gid == test_gid, (
                f"Container GID {actual_gid} does not match MAGPIE_GID {test_gid}"
            )

        finally:
            # Cleanup
            subprocess.run(
                ["docker", "rmi", "magpie-privtest-env:latest"],
                capture_output=True,
            )

    def test_container_validates_invalid_uid(self) -> None:
        """Test that container rejects invalid UID values."""
        # Build the image
        subprocess.run(
            ["docker", "build", "-t", "magpie-privtest-invalid:latest", "."],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
        )

        try:
            # Test with invalid UID (negative number)
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-e",
                    "MAGPIE_UID=-1",
                    "-e",
                    "MAGPIE_GID=1000",
                    "magpie-privtest-invalid:latest",
                    "sh",
                    "-c",
                    "echo 'should not reach here'",
                ],
                capture_output=True,
                text=True,
            )

            assert result.returncode != 0, "Container should fail with invalid UID"
            assert "Invalid RUN_UID" in result.stderr, (
                f"Expected error message about invalid UID, got: {result.stderr}"
            )

        finally:
            # Cleanup
            subprocess.run(
                ["docker", "rmi", "magpie-privtest-invalid:latest"],
                capture_output=True,
            )

    def test_container_validates_invalid_gid(self) -> None:
        """Test that container rejects invalid GID values."""
        # Build the image
        subprocess.run(
            ["docker", "build", "-t", "magpie-privtest-invalidgid:latest", "."],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
        )

        try:
            # Test with invalid GID (non-numeric)
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-e",
                    "MAGPIE_UID=1000",
                    "-e",
                    "MAGPIE_GID=invalid",
                    "magpie-privtest-invalidgid:latest",
                    "sh",
                    "-c",
                    "echo 'should not reach here'",
                ],
                capture_output=True,
                text=True,
            )

            assert result.returncode != 0, "Container should fail with invalid GID"
            assert "Invalid RUN_GID" in result.stderr, (
                f"Expected error message about invalid GID, got: {result.stderr}"
            )

        finally:
            # Cleanup
            subprocess.run(
                ["docker", "rmi", "magpie-privtest-invalidgid:latest"],
                capture_output=True,
            )

    def test_container_runs_as_root_when_uid_zero(self) -> None:
        """Test that container runs as root when UID=0 (no privilege drop)."""
        # Build the image
        subprocess.run(
            ["docker", "build", "-t", "magpie-privtest-root:latest", "."],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
        )

        try:
            # Run with UID=0
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-e",
                    "MAGPIE_UID=0",
                    "-e",
                    "MAGPIE_GID=0",
                    "magpie-privtest-root:latest",
                    "sh",
                    "-c",
                    "id -u && id -g && whoami",
                ],
                capture_output=True,
                text=True,
                check=True,
            )

            lines = result.stdout.strip().split("\n")
            actual_uid = int(lines[0])
            actual_gid = int(lines[1])
            username = lines[2]

            assert actual_uid == 0, "Container should run as UID 0 (root)"
            assert actual_gid == 0, "Container should run as GID 0 (root)"
            assert username == "root", f"Expected root user, got: {username}"

        finally:
            # Cleanup
            subprocess.run(
                ["docker", "rmi", "magpie-privtest-root:latest"],
                capture_output=True,
            )

    def test_container_creates_magpie_user_file(self) -> None:
        """Test that /run/magpie-user file is created with correct content."""
        # Build the image
        subprocess.run(
            ["docker", "build", "-t", "magpie-privtest-userfile:latest", "."],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
        )

        try:
            test_uid = 3000
            test_gid = 3001

            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-e",
                    f"MAGPIE_UID={test_uid}",
                    "-e",
                    f"MAGPIE_GID={test_gid}",
                    "magpie-privtest-userfile:latest",
                    "sh",
                    "-c",
                    "cat /run/magpie-user && stat -c '%a' /run/magpie-user",
                ],
                capture_output=True,
                text=True,
                check=True,
            )

            lines = result.stdout.strip().split("\n")
            file_content = lines[0]
            file_perms = lines[1]

            assert file_content == f"{test_uid}:{test_gid}", (
                f"Expected /run/magpie-user content '{test_uid}:{test_gid}', got: {file_content}"
            )
            assert file_perms == "644", (
                f"Expected /run/magpie-user permissions 644, got: {file_perms}"
            )

        finally:
            # Cleanup
            subprocess.run(
                ["docker", "rmi", "magpie-privtest-userfile:latest"],
                capture_output=True,
            )

    def test_container_fallback_to_default_uid_gid(self) -> None:
        """Test that container falls back to 1000:1000 when /data doesn't exist."""
        # Build the image
        subprocess.run(
            ["docker", "build", "-t", "magpie-privtest-fallback:latest", "."],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
        )

        try:
            # Run without mounting /data volume and without env vars
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "magpie-privtest-fallback:latest",
                    "sh",
                    "-c",
                    "id -u && id -g",
                ],
                capture_output=True,
                text=True,
                check=True,
            )

            lines = result.stdout.strip().split("\n")
            actual_uid = int(lines[0])
            actual_gid = int(lines[1])

            assert actual_uid == 1000, (
                f"Container should fall back to UID 1000, got: {actual_uid}"
            )
            assert actual_gid == 1000, (
                f"Container should fall back to GID 1000, got: {actual_gid}"
            )

        finally:
            # Cleanup
            subprocess.run(
                ["docker", "rmi", "magpie-privtest-fallback:latest"],
                capture_output=True,
            )

    def test_entrypoint_with_data_fallback(self) -> None:
        """Test UID/GID detection falls back from /data/artifacts to /data."""
        # Build the image
        subprocess.run(
            ["docker", "build", "-t", "magpie-privtest-datafallback:latest", "."],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
        )

        try:
            # Create a temp directory for /data (but not /data/artifacts)
            with tempfile.TemporaryDirectory(prefix="magpie_fallback_") as temp_dir:
                temp_path = Path(temp_dir)

                # Get UID/GID from this directory
                stat_info = os.stat(temp_path)
                expected_uid = stat_info.st_uid
                expected_gid = stat_info.st_gid

                # Mount only /data (not /data/artifacts), so entrypoint must fall back to /data
                result = subprocess.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "-v",
                        f"{temp_path}:/data",
                        "magpie-privtest-datafallback:latest",
                        "sh",
                        "-c",
                        "id -u && id -g",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )

                lines = result.stdout.strip().split("\n")
                actual_uid = int(lines[0])
                actual_gid = int(lines[1])

                assert actual_uid == expected_uid, (
                    f"Container UID {actual_uid} does not match /data ownership {expected_uid}"
                )
                assert actual_gid == expected_gid, (
                    f"Container GID {actual_gid} does not match /data ownership {expected_gid}"
                )

        finally:
            # Cleanup
            subprocess.run(
                ["docker", "rmi", "magpie-privtest-datafallback:latest"],
                capture_output=True,
            )
