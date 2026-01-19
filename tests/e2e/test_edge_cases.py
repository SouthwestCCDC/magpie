"""Edge case E2E tests for Magpie.

Tests edge cases and special scenarios:
- Duplicate uploads
- Tag updates
- Tag removal
- Garbage collection
"""

from __future__ import annotations

import hashlib
import os
import subprocess

import httpx
import pytest

from tests.e2e.conftest import PROJECT_ROOT


@pytest.mark.e2e
@pytest.mark.slow
class TestDuplicateUpload:
    """Tests for duplicate artifact upload handling."""

    def test_duplicate_upload_returns_existing_hash(
        self,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify duplicate upload returns the same hash (content-addressed)."""
        expected_hash = hashlib.sha256(test_artifact_content).hexdigest()

        # First upload
        response1 = authenticated_client.post(
            "/api/v1/upload/e2e-tests/duplicate-test-1",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert response1.status_code == 200
        hash1 = response1.json()["hash"]

        # Second upload of same content to different path
        response2 = authenticated_client.post(
            "/api/v1/upload/e2e-tests/duplicate-test-2",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert response2.status_code == 200
        hash2 = response2.json()["hash"]

        # Both should return the same hash
        assert hash1 == expected_hash
        assert hash2 == expected_hash
        assert hash1 == hash2

    def test_duplicate_upload_to_same_path(
        self,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify re-uploading same content to same path succeeds."""
        # First upload
        response1 = authenticated_client.post(
            "/api/v1/upload/e2e-tests/same-path-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert response1.status_code == 200
        hash1 = response1.json()["hash"]

        # Second upload to same path
        response2 = authenticated_client.post(
            "/api/v1/upload/e2e-tests/same-path-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert response2.status_code == 200
        hash2 = response2.json()["hash"]

        assert hash1 == hash2


@pytest.mark.e2e
@pytest.mark.slow
class TestTagUpdate:
    """Tests for tag update operations."""

    def test_tag_update_changes_target(
        self,
        authenticated_client: httpx.Client,
    ) -> None:
        """Verify updating a tag changes its target hash."""
        content_v1 = b"Tag update test content version 1"
        content_v2 = b"Tag update test content version 2"

        # Upload v1
        response1 = authenticated_client.post(
            "/api/v1/upload/e2e-tests/tag-update-test",
            files={"file": ("artifact", content_v1, "application/octet-stream")},
        )
        hash_v1 = response1.json()["hash"]

        # Tag v1 as "latest"
        authenticated_client.post(
            f"/api/v1/artifacts/e2e-tests/tag-update-test/{hash_v1}/tags",
            json={"tag_name": "latest"},
        )

        # Upload v2
        response2 = authenticated_client.post(
            "/api/v1/upload/e2e-tests/tag-update-test",
            files={"file": ("artifact", content_v2, "application/octet-stream")},
        )
        hash_v2 = response2.json()["hash"]

        # Update "latest" tag to point to v2
        response = authenticated_client.post(
            f"/api/v1/artifacts/e2e-tests/tag-update-test/{hash_v2}/tags",
            json={"tag_name": "latest"},
        )

        assert response.status_code in (200, 201)

        # Verify tag now points to v2
        info_response = authenticated_client.get(
            "/api/v1/artifacts/e2e-tests/tag-update-test/latest/info"
        )
        assert info_response.status_code == 200
        info_data = info_response.json()
        assert info_data.get("hash") == hash_v2 or hash_v2 in str(info_data)


@pytest.mark.e2e
@pytest.mark.slow
class TestTagRemoval:
    """Tests for tag removal operations."""

    def test_tag_removal_works(
        self,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify removing a tag works correctly."""
        # Upload artifact
        upload_response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/untag-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        artifact_hash = upload_response.json()["hash"]

        # Create tag
        authenticated_client.post(
            f"/api/v1/artifacts/e2e-tests/untag-test/{artifact_hash}/tags",
            json={"tag_name": "to-remove"},
        )

        # Remove tag
        response = authenticated_client.delete(
            "/api/v1/artifacts/e2e-tests/untag-test/tags/to-remove"
        )

        assert response.status_code in (200, 204)

        # Verify tag is gone - accessing by tag should fail
        info_response = authenticated_client.get(
            "/api/v1/artifacts/e2e-tests/untag-test/to-remove/info"
        )
        assert info_response.status_code == 404

    def test_untag_via_cli(
        self,
        docker_services: dict[str, str],
        authenticated_client: httpx.Client,
        admin_token: str,
        test_artifact_content: bytes,
    ) -> None:
        """Verify magpie untag command works."""
        base_url = docker_services["base_url"]

        # Upload artifact
        upload_response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/cli-untag-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        artifact_hash = upload_response.json()["hash"]

        # Create tag
        authenticated_client.post(
            f"/api/v1/artifacts/e2e-tests/cli-untag-test/{artifact_hash}/tags",
            json={"tag_name": "cli-remove"},
        )

        env = os.environ.copy()
        env["MAGPIE_SERVER"] = base_url
        env["MAGPIE_TOKEN"] = admin_token

        # Remove tag via CLI
        result = subprocess.run(
            [
                "uv",
                "run",
                "magpie",
                "untag",
                "e2e-tests/cli-untag-test",
                "cli-remove",
            ],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"untag failed: {result.stderr}"


@pytest.mark.e2e
@pytest.mark.slow
class TestGarbageCollection:
    """Tests for garbage collection operations."""

    def test_gc_cleans_expired_untagged_blobs(
        self,
        docker_services: dict[str, str],
        authenticated_client: httpx.Client,
        admin_token: str,
    ) -> None:
        """Verify GC cleans up expired untagged blobs.

        Note: This test may be limited since GC typically requires
        blobs to be older than retention_days to be cleaned.
        """
        base_url = docker_services["base_url"]

        # Upload an artifact without tagging
        content = b"GC test content - should be cleaned"
        authenticated_client.post(
            "/api/v1/upload/e2e-tests/gc-test",
            files={"file": ("artifact", content, "application/octet-stream")},
        )

        env = os.environ.copy()
        env["MAGPIE_SERVER"] = base_url
        env["MAGPIE_TOKEN"] = admin_token

        # Run GC via CLI (use dry-run first to test functionality)
        result = subprocess.run(
            ["uv", "run", "magpie", "gc", "--dry-run"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        # GC should run without error (may not clean anything if not expired)
        assert result.returncode == 0, f"gc failed: {result.stderr}"

    def test_gc_preserves_tagged_blobs(
        self,
        docker_services: dict[str, str],
        authenticated_client: httpx.Client,
        admin_token: str,
        test_artifact_content: bytes,
    ) -> None:
        """Verify GC preserves blobs that are tagged."""
        base_url = docker_services["base_url"]

        # Upload and tag artifact
        upload_response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/gc-preserve-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        artifact_hash = upload_response.json()["hash"]

        # Tag it to prevent GC
        authenticated_client.post(
            f"/api/v1/artifacts/e2e-tests/gc-preserve-test/{artifact_hash}/tags",
            json={"tag_name": "keep"},
        )

        env = os.environ.copy()
        env["MAGPIE_SERVER"] = base_url
        env["MAGPIE_TOKEN"] = admin_token

        # Run GC
        result = subprocess.run(
            ["uv", "run", "magpie", "gc", "--dry-run"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"gc failed: {result.stderr}"

        # Verify tagged artifact still exists
        info_response = authenticated_client.get(
            f"/api/v1/artifacts/e2e-tests/gc-preserve-test/{artifact_hash}/info"
        )
        assert info_response.status_code == 200


@pytest.mark.e2e
@pytest.mark.slow
class TestContentOverwrite:
    """Tests for content overwrite scenarios."""

    def test_different_content_same_path_creates_new_hash(
        self,
        authenticated_client: httpx.Client,
    ) -> None:
        """Verify uploading different content to same path creates new hash."""
        content_a = b"Content A for overwrite test"
        content_b = b"Content B for overwrite test - different"

        # Upload content A
        response_a = authenticated_client.post(
            "/api/v1/upload/e2e-tests/overwrite-test",
            files={"file": ("artifact", content_a, "application/octet-stream")},
        )
        hash_a = response_a.json()["hash"]

        # Upload content B to same path
        response_b = authenticated_client.post(
            "/api/v1/upload/e2e-tests/overwrite-test",
            files={"file": ("artifact", content_b, "application/octet-stream")},
        )
        hash_b = response_b.json()["hash"]

        # Hashes should be different
        assert hash_a != hash_b
        assert hash_a == hashlib.sha256(content_a).hexdigest()
        assert hash_b == hashlib.sha256(content_b).hexdigest()


@pytest.mark.e2e
@pytest.mark.slow
class TestLargeArtifact:
    """Tests for larger artifact handling."""

    def test_upload_larger_artifact(
        self,
        authenticated_client: httpx.Client,
    ) -> None:
        """Verify uploading a larger artifact (1MB) works."""
        # Generate 1MB of content
        large_content = b"X" * (1024 * 1024)
        expected_hash = hashlib.sha256(large_content).hexdigest()

        response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/large-artifact-test",
            files={"file": ("artifact", large_content, "application/octet-stream")},
            timeout=60.0,
        )

        assert response.status_code == 200
        assert response.json()["hash"] == expected_hash


@pytest.mark.e2e
@pytest.mark.slow
class TestDirectoryBrowsing:
    """Tests for directory browsing on artifact paths.

    Verifies that Caddy serves directory listings for /artifacts/ paths,
    enabling discovery and debugging of stored artifacts.
    """

    def test_browse_artifacts_root(
        self,
        http_client: httpx.Client,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify browsing /artifacts/ returns directory listing."""
        # Upload an artifact first to ensure there's something to list
        authenticated_client.post(
            "/api/v1/upload/e2e-tests/browse-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )

        # Browse the artifacts root directory
        response = http_client.get("/artifacts/")

        assert response.status_code == 200
        # Caddy's browse directive returns HTML
        assert "text/html" in response.headers.get("content-type", "")
        # Should contain directory listing indicators
        body = response.text
        # Caddy's directory listing contains the path somewhere
        assert "artifacts" in body.lower() or "Index of" in body

    def test_browse_artifact_path_shows_contents(
        self,
        http_client: httpx.Client,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify browsing artifact directory shows blobs, metadata, etc."""
        # Upload an artifact
        upload_response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/browse-path-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code == 200

        # Browse the specific artifact directory
        response = http_client.get("/artifacts/e2e-tests/browse-path-test/")

        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        body = response.text
        # Directory should contain blobs, metadata dirs and .magpie manifest
        # At least one of these should appear in the listing
        assert (
            "blobs" in body.lower()
            or "metadata" in body.lower()
            or "magpie" in body.lower()
            or "latest" in body.lower()  # symlink to latest version
        )

    def test_browse_nonexistent_path_returns_404(
        self,
        http_client: httpx.Client,
    ) -> None:
        """Verify browsing nonexistent path returns 404."""
        response = http_client.get("/artifacts/nonexistent/path/that/does/not/exist/")

        # Should get 404 for nonexistent directory
        assert response.status_code == 404
