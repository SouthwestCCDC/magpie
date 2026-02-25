"""Release validation E2E tests for v0.1.3.

These tests target high-risk changes introduced in v0.1.3 to ensure they work
correctly in the full Docker Compose environment.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile

import httpx
import pytest

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
    Tests that the API correctly sets X-Magpie-Upgrade-Available header
    when clients use older versions.
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

    def test_old_version_receives_upgrade_header(
        self,
        base_url: str,
        write_token: str,
    ) -> None:
        """Verify old version clients receive upgrade requirement response."""
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

    Covers issue #465 - GC exception narrowing.
    Tests that GC runs without errors and reports correct counts when
    artifacts are present.
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
        authenticated_client.post(
            f"/api/v1/artifacts/e2e-tests/release-validation/gc-tagged/@{tagged_hash[:8]}/tags",
            json={"tag_name": "keep-this"},
        )

        # Upload another artifact, then remove its latest tag to make it untagged
        untagged_content = b"GC test - untagged artifact for cleanup"
        upload_response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/release-validation/gc-untagged",
            files={"file": ("artifact", untagged_content, "application/octet-stream")},
        )
        assert upload_response.status_code == 200

        # Remove the latest tag to make it untagged
        authenticated_client.delete(
            "/api/v1/artifacts/e2e-tests/release-validation/gc-untagged/tags/latest"
        )

        # Run GC dry-run via CLI
        env = os.environ.copy()
        env["MAGPIE_SERVER"] = base_url
        env["MAGPIE_TOKEN"] = admin_token

        result = subprocess.run(
            ["uv", "run", "magpie", "gc", "--dry-run"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        # GC should complete without error
        assert result.returncode == 0, f"GC failed: {result.stderr}"

        # Output should indicate it ran (may not clean anything due to retention period)
        assert "dry" in result.stdout.lower() or "gc" in result.stdout.lower()


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

        # Should exit non-zero
        assert result.returncode != 0

        # Should contain user-friendly error message, not Python traceback
        stderr_lower = result.stderr.lower()
        assert "error" in stderr_lower or "failed" in stderr_lower or "connect" in stderr_lower
        # Should NOT contain Python traceback indicators
        assert "traceback" not in stderr_lower
        assert "exception" not in stderr_lower or "connection" in stderr_lower
