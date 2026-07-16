"""Release validation E2E tests for v0.1.3.

These tests target high-risk changes introduced in v0.1.3 to ensure they work
correctly in the full Docker Compose environment.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile

import httpx
import pytest
from packaging.version import parse

from magpie import __version__
from magpie.cli.formatting import ExitCode
from magpie.server.middleware import get_min_client_version
from tests.e2e.conftest import PROJECT_ROOT, create_token_via_api


@pytest.mark.e2e
@pytest.mark.slow
class TestStreamingUploadIntegrity:
    """Validates streaming upload integrity through Caddy + FastAPI.

    Covers issue #455 - upload double-buffering fix.
    Tests that streaming uploads maintain data integrity from CLI through
    the full stack (Caddy reverse proxy -> FastAPI -> filesystem).
    """

    def test_small_file_upload_integrity(
        self,
        docker_services: dict[str, str],
        write_token: str,
    ) -> None:
        """Verify small file upload maintains SHA-256 integrity."""
        base_url = docker_services["base_url"]

        # Generate small test file (~1KB)
        content = b"Release validation test - small file\n" * 30  # ~1KB
        expected_hash = hashlib.sha256(content).hexdigest()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(content)
            temp_file = f.name

        try:
            # Push via CLI
            env = os.environ.copy()
            env["MAGPIE_SERVER"] = base_url
            env["MAGPIE_TOKEN"] = write_token

            result = subprocess.run(
                [
                    "uv",
                    "run",
                    "magpie",
                    "push",
                    temp_file,
                    "--to",
                    "e2e-tests/release-validation/upload-small",
                ],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )

            assert result.returncode == 0, f"Push failed: {result.stderr}"

            # Download via API and verify hash
            with httpx.Client(base_url=base_url, timeout=30.0) as client:
                response = client.get(
                    "/artifacts/e2e-tests/release-validation/upload-small/latest",
                    headers={"Authorization": f"Bearer {write_token}"},
                )
                assert response.status_code == 200

                downloaded_content = response.content
                downloaded_hash = hashlib.sha256(downloaded_content).hexdigest()

                assert downloaded_hash == expected_hash, (
                    f"Hash mismatch: expected {expected_hash}, got {downloaded_hash}"
                )
                assert downloaded_content == content

        finally:
            os.unlink(temp_file)

    def test_large_file_upload_integrity(
        self,
        docker_services: dict[str, str],
        write_token: str,
    ) -> None:
        """Verify large file (~50MB) upload maintains SHA-256 integrity."""
        base_url = docker_services["base_url"]

        # Generate ~50MB test file
        chunk = b"X" * 1024  # 1KB chunk
        content = chunk * (50 * 1024)  # 50MB
        expected_hash = hashlib.sha256(content).hexdigest()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(content)
            temp_file = f.name

        try:
            # Push via CLI
            env = os.environ.copy()
            env["MAGPIE_SERVER"] = base_url
            env["MAGPIE_TOKEN"] = write_token

            result = subprocess.run(
                [
                    "uv",
                    "run",
                    "magpie",
                    "push",
                    temp_file,
                    "--to",
                    "e2e-tests/release-validation/upload-large",
                ],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,  # Allow extra time for 50MB
            )

            assert result.returncode == 0, f"Push failed: {result.stderr}"

            # Download via API and verify hash
            with httpx.Client(base_url=base_url, timeout=120.0) as client:
                response = client.get(
                    "/artifacts/e2e-tests/release-validation/upload-large/latest",
                    headers={"Authorization": f"Bearer {write_token}"},
                )
                assert response.status_code == 200

                downloaded_content = response.content
                downloaded_hash = hashlib.sha256(downloaded_content).hexdigest()

                assert downloaded_hash == expected_hash, (
                    f"Hash mismatch: expected {expected_hash}, got {downloaded_hash}"
                )

        finally:
            os.unlink(temp_file)


@pytest.mark.e2e
@pytest.mark.slow
class TestVersionMiddleware:
    """Validates version-check middleware header behavior.

    Covers issues #451, #496 - server version check middleware.
    Tests that every response is stamped with X-Magpie-Server-Version (#451),
    that clients below the minimum compatible version are rejected with 426
    and X-Magpie-Min-Client-Version, and that clients which are compatible but
    below the current server version receive X-Magpie-Upgrade-Available (#496).
    """

    def test_current_version_no_upgrade_header(
        self,
        base_url: str,
        write_token: str,
    ) -> None:
        """Verify current version clients succeed without upgrade requirement."""
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            response = client.get(
                "/api/v1/artifacts",
                headers={
                    "Authorization": f"Bearer {write_token}",
                    "User-Agent": "magpie-cli/0.1.3",
                },
            )

            # Current versions succeed with 200
            assert response.status_code == 200
            # Every response is stamped with the server version (issue #451)
            assert response.headers.get("X-Magpie-Server-Version") == __version__
            # A client at/above the server version is not "outdated"
            assert "X-Magpie-Upgrade-Available" not in response.headers

    def test_outdated_but_compatible_version_receives_upgrade_available_header(
        self,
        base_url: str,
        write_token: str,
    ) -> None:
        """Verify a compatible-but-outdated client receives X-Magpie-Upgrade-Available.

        Covers issue #496. The client version used here is exactly the minimum
        compatible version derived from the running server version, so it is
        guaranteed to pass the "too old, rejected" check but still be older
        than the server - the actual trigger condition for the upgrade-available
        header (magpie.server.middleware.VersionCheckMiddleware.dispatch).
        """
        min_client_version = get_min_client_version(__version__)
        assert parse(min_client_version) < parse(__version__), (
            "test precondition violated: min_client_version must be strictly "
            "less than the running server version to exercise the "
            "compatible-but-outdated path"
        )

        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            response = client.get(
                "/api/v1/artifacts",
                headers={
                    "Authorization": f"Bearer {write_token}",
                    "User-Agent": f"magpie-cli/{min_client_version}",
                },
            )

            # Compatible clients still succeed with 200 (not rejected)
            assert response.status_code == 200
            assert response.headers.get("X-Magpie-Upgrade-Available") == __version__

    def test_below_min_version_receives_426_and_min_version_header(
        self,
        base_url: str,
        write_token: str,
    ) -> None:
        """Verify clients below the minimum compatible version are rejected with 426."""
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            response = client.get(
                "/api/v1/artifacts",
                headers={
                    "Authorization": f"Bearer {write_token}",
                    "User-Agent": "magpie-cli/0.0.1",
                },
            )

            # Old versions get 426 Upgrade Required
            assert response.status_code == 426
            # Should include minimum version requirement header
            assert "X-Magpie-Min-Client-Version" in response.headers

    def test_no_user_agent_still_succeeds(
        self,
        base_url: str,
        write_token: str,
    ) -> None:
        """Verify requests without User-Agent still work (don't break curl/etc)."""
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            # httpx sets a default User-Agent, so we need to explicitly remove it
            response = client.get(
                "/api/v1/artifacts",
                headers={
                    "Authorization": f"Bearer {write_token}",
                    "User-Agent": "",
                },
            )

            assert response.status_code == 200


@pytest.mark.e2e
@pytest.mark.slow
class TestTokenLifecycle:
    """Validates token lifecycle operations.

    Covers issue #426 - TokenService singleton refactor.
    Tests token creation, rotation, and revocation via API.
    """

    def test_token_creation_and_usage(
        self,
        base_url: str,
        admin_token: str,
    ) -> None:
        """Verify token creation and basic usage."""
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            # Create token
            token = create_token_via_api(
                client, admin_token, "e2e-release-validation-creation", "read"
            )
            assert token.startswith("mgp_")

            # Use token to list artifacts
            response = client.get(
                "/api/v1/artifacts",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200

    def test_token_rotation(
        self,
        base_url: str,
        admin_token: str,
    ) -> None:
        """Verify token rotation invalidates old token and creates new one."""
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            # Create initial token
            old_token = create_token_via_api(
                client, admin_token, "e2e-release-validation-rotation", "read"
            )

            # Verify old token works
            response = client.get(
                "/api/v1/artifacts",
                headers={"Authorization": f"Bearer {old_token}"},
            )
            assert response.status_code == 200

            # Rotate token
            rotate_response = client.post(
                "/api/v1/tokens/e2e-release-validation-rotation/rotate",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert rotate_response.status_code == 200
            new_token = rotate_response.json()["token"]
            assert new_token != old_token

            # Verify old token no longer works
            response = client.get(
                "/api/v1/artifacts",
                headers={"Authorization": f"Bearer {old_token}"},
            )
            assert response.status_code == 401

            # Verify new token works
            response = client.get(
                "/api/v1/artifacts",
                headers={"Authorization": f"Bearer {new_token}"},
            )
            assert response.status_code == 200

    def test_token_revocation(
        self,
        base_url: str,
        admin_token: str,
    ) -> None:
        """Verify token revocation prevents further usage."""
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            # Create token
            token = create_token_via_api(
                client, admin_token, "e2e-release-validation-revocation", "read"
            )

            # Verify token works
            response = client.get(
                "/api/v1/artifacts",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200

            # Revoke token
            revoke_response = client.delete(
                "/api/v1/tokens/e2e-release-validation-revocation",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert revoke_response.status_code in (200, 204)

            # Verify revoked token no longer works
            response = client.get(
                "/api/v1/artifacts",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 401


@pytest.mark.e2e
@pytest.mark.slow
class TestGarbageCollectionDryRun:
    """Validates garbage collection dry-run with artifacts present.

    Covers issue #465 - GC exception narrowing. `test_gc_dry_run_with_tagged_and_untagged`
    exercises the ordinary scan/report path; `test_gc_dry_run_skips_corrupt_manifest`
    actually corrupts a manifest on disk so the narrowed exception handler in
    magpie.storage.gc._scan_artifacts is exercised, not just the happy path.
    """

    def test_gc_dry_run_with_tagged_and_untagged(
        self,
        docker_services: dict[str, str],
        authenticated_client: httpx.Client,
        admin_token: str,
    ) -> None:
        """Verify GC dry-run completes without error and reports counts correctly."""
        base_url = docker_services["base_url"]

        # Upload and tag one artifact
        tagged_content = b"GC test - tagged artifact for preservation"
        upload_response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/release-validation/gc-tagged",
            files={"file": ("artifact", tagged_content, "application/octet-stream")},
        )
        assert upload_response.status_code == 200
        tagged_hash = upload_response.json()["hash"]

        # Create a named tag to ensure it's protected
        tag_response = authenticated_client.post(
            f"/api/v1/artifacts/e2e-tests/release-validation/gc-tagged/@{tagged_hash[:8]}/tags",
            json={"tag_name": "keep-this"},
        )
        assert tag_response.status_code in (200, 201)

        # Upload another artifact, then remove its latest tag to make it untagged
        untagged_content = b"GC test - untagged artifact for cleanup"
        upload_response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/release-validation/gc-untagged",
            files={"file": ("artifact", untagged_content, "application/octet-stream")},
        )
        assert upload_response.status_code == 200

        # Remove the latest tag to make it untagged
        untag_response = authenticated_client.delete(
            "/api/v1/artifacts/e2e-tests/release-validation/gc-untagged/tags/latest"
        )
        assert untag_response.status_code in (200, 204)

        # Run GC dry-run via CLI, using JSON output so we can assert on the
        # actual reported counts instead of grepping human-readable text (the
        # CLI unconditionally prints "GC Preview (dry run):" regardless of
        # what was actually found, so that text alone proves nothing).
        env = os.environ.copy()
        env["MAGPIE_SERVER"] = base_url
        env["MAGPIE_TOKEN"] = admin_token

        result = subprocess.run(
            ["uv", "run", "magpie", "--format", "json", "gc", "--dry-run"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        # GC should complete without error
        assert result.returncode == 0, f"GC failed: {result.stderr}"

        gc_data = json.loads(result.stdout)["data"]
        assert gc_data["dry_run"] is True
        # Both artifacts uploaded above must have been scanned and their blobs
        # counted (other e2e tests may add more, so these are lower bounds).
        assert gc_data["artifacts_scanned"] >= 2
        assert gc_data["blobs_found"] >= 2

    def test_gc_dry_run_skips_corrupt_manifest(
        self,
        docker_services: dict[str, str],
        authenticated_client: httpx.Client,
        admin_token: str,
    ) -> None:
        """Verify GC survives a corrupt manifest instead of failing the whole run.

        Covers issue #465 directly: the corrupt-manifest handler in
        magpie.storage.gc._scan_artifacts must catch ManifestCorruptError/OSError
        for a single artifact and continue scanning, not let the exception
        propagate and abort the entire GC run. This actually corrupts a
        manifest file on disk (via docker exec into the running container) so
        the handler is exercised - a test that only uploads valid artifacts,
        as the happy-path test above does, never reaches this code path at all.
        """
        base_url = docker_services["base_url"]
        artifact_path = "e2e-tests/release-validation/gc-corrupt-manifest"

        # Upload an artifact so a real .magpie manifest exists on disk.
        upload_response = authenticated_client.post(
            f"/api/v1/upload/{artifact_path}",
            files={"file": ("artifact", b"GC test - corrupt manifest", "application/octet-stream")},
        )
        assert upload_response.status_code == 200

        # Corrupt the manifest in place inside the running container so the
        # next GC scan hits ManifestCorruptError (invalid JSON) when reading it.
        compose_cmd = ["docker", "compose", "-f", str(PROJECT_ROOT / "docker-compose.yml")]
        docker_env = os.environ.copy()
        docker_env["COMPOSE_PROJECT_NAME"] = docker_services["project_name"]
        manifest_path = f"/data/artifacts/{artifact_path}/.magpie"

        corrupt_result = subprocess.run(
            [
                *compose_cmd,
                "exec",
                "-T",
                "magpie",
                "sh",
                "-c",
                f"echo 'not valid json' > {manifest_path}",
            ],
            cwd=PROJECT_ROOT,
            env=docker_env,
            capture_output=True,
            text=True,
        )
        assert corrupt_result.returncode == 0, (
            f"Failed to corrupt manifest: {corrupt_result.stderr}"
        )

        # GC dry-run must complete successfully despite the corrupt manifest -
        # it should skip that artifact and continue, not 500 the whole request.
        cli_env = os.environ.copy()
        cli_env["MAGPIE_SERVER"] = base_url
        cli_env["MAGPIE_TOKEN"] = admin_token

        result = subprocess.run(
            ["uv", "run", "magpie", "--format", "json", "gc", "--dry-run"],
            cwd=PROJECT_ROOT,
            env=cli_env,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"GC failed on corrupt manifest: {result.stderr}"

        gc_data = json.loads(result.stdout)["data"]
        # The corrupt-manifest artifact is still counted as scanned (the
        # counter increments before the manifest is read), confirming GC
        # actually reached it and recovered rather than aborting beforehand.
        assert gc_data["artifacts_scanned"] >= 1


@pytest.mark.e2e
@pytest.mark.slow
class TestTagLifecycleAndStorage:
    """Validates tag lifecycle and storage deduplication.

    Covers issue #470 - storage deduplication refactor.
    Tests that tags work correctly with the deduplicated storage layer.
    """

    def test_tag_lifecycle_with_dedup_storage(
        self,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify complete tag lifecycle works with deduplicated storage."""
        # Upload artifact
        upload_response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/release-validation/tag-lifecycle",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code == 200
        artifact_hash = upload_response.json()["hash"]

        # Create named tag
        tag_response = authenticated_client.post(
            f"/api/v1/artifacts/e2e-tests/release-validation/tag-lifecycle/@{artifact_hash[:8]}/tags",
            json={"tag_name": "reg2026"},
        )
        assert tag_response.status_code in (200, 201)

        # Verify download works via named tag
        response = authenticated_client.get(
            "/artifacts/e2e-tests/release-validation/tag-lifecycle/reg2026"
        )
        assert response.status_code == 200
        assert response.content == test_artifact_content

        # Untag it
        untag_response = authenticated_client.delete(
            "/api/v1/artifacts/e2e-tests/release-validation/tag-lifecycle/tags/reg2026"
        )
        assert untag_response.status_code in (200, 204)

        # Verify tag no longer resolves
        response = authenticated_client.get(
            "/artifacts/e2e-tests/release-validation/tag-lifecycle/reg2026"
        )
        assert response.status_code == 404

        # Verify artifact info still accessible via hash ref (API endpoint)
        info_response = authenticated_client.get(
            f"/api/v1/artifacts/e2e-tests/release-validation/tag-lifecycle/@{artifact_hash[:8]}/info"
        )
        assert info_response.status_code == 200
        assert info_response.json()["hash"] == artifact_hash


@pytest.mark.e2e
class TestCLINetworkErrorHandling:
    """Validates CLI network error handling.

    Covers issue #443 - CLI error handling improvements.
    Tests that CLI gracefully handles network errors with user-friendly messages.
    """

    def test_cli_connection_refused_error(self) -> None:
        """Verify CLI handles connection refused with user-friendly error."""
        # Point CLI at non-existent server
        env = os.environ.copy()
        env["MAGPIE_SERVER"] = "http://localhost:19999"
        env["MAGPIE_TOKEN"] = "fake_token_for_testing"

        result = subprocess.run(
            ["uv", "run", "magpie", "ls"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        # Issue #443 mandates exit code 2 (ExitCode.NETWORK_ERROR) specifically
        # for network errors, so scripts can differentiate them from other
        # failures - not merely "some nonzero code".
        assert result.returncode == ExitCode.NETWORK_ERROR

        # Should contain user-friendly error message, not Python traceback
        stderr_lower = result.stderr.lower()
        assert "error" in stderr_lower or "failed" in stderr_lower or "connect" in stderr_lower
        # Should NOT contain Python traceback indicators
        assert "traceback" not in stderr_lower
        assert "exception" not in stderr_lower
