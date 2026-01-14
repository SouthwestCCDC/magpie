"""Security injection tests for magpie.

This module tests that the magpie server properly rejects malicious inputs
including SQL injection attempts, Unicode attacks, symlink attacks, path
traversal, and null byte injection.

These tests verify that security validation in src/magpie/storage/paths.py
and the server routes correctly handle potentially malicious input.

Note: These tests focus on ensuring that malicious payloads cannot achieve
their intended attack goals. The specific HTTP status code may vary (400, 401,
404, 422, 500) depending on where the payload is rejected - the key is that
the attack does not succeed.

AI-assisted: Generated with Claude Code (Opus 4.5).
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magpie.storage.exceptions import InvalidArtifactPathError
from magpie.storage.paths import normalize_artifact_path
from magpie.storage.service import StorageService

# =============================================================================
# SQL Injection Tests
# =============================================================================


class TestSQLInjectionArtifactPaths:
    """Test SQL injection attempts in artifact paths.

    While magpie uses file-based storage rather than SQL, SQL metacharacters
    could still cause issues if paths are used in shell commands or logging.
    These tests verify that such inputs are either safely handled or rejected.
    """

    SQL_INJECTION_PAYLOADS = [
        "'; DROP TABLE artifacts; --",
        "1'; DELETE FROM tokens WHERE '1'='1",
        "test' OR '1'='1",
        "test' UNION SELECT * FROM users --",
        'test"; DROP TABLE artifacts; --',
        "1; SELECT * FROM tokens",
        "test' AND 1=1 --",
        "test') OR ('1'='1",
        "test'; EXEC xp_cmdshell('whoami'); --",
    ]

    @pytest.mark.parametrize("payload", SQL_INJECTION_PAYLOADS)
    def test_sql_injection_in_artifact_info_no_db_errors(
        self, client: TestClient, payload: str
    ) -> None:
        """SQL injection payloads should not expose database errors."""
        response = client.get(f"/api/v1/artifacts/{payload}/latest/info")

        # Should not return 200 (success) for these malicious paths
        assert response.status_code != 200
        # Should never expose database errors or SQL syntax
        response_text = response.text.lower()
        assert "sql" not in response_text
        assert "sqlalchemy" not in response_text
        assert "database error" not in response_text
        assert "syntax error" not in response_text

    @pytest.mark.parametrize("payload", SQL_INJECTION_PAYLOADS)
    def test_sql_injection_in_list_prefix(self, client: TestClient, payload: str) -> None:
        """SQL injection payloads in list prefix should not expose errors."""
        response = client.get("/api/v1/artifacts", params={"prefix": payload})

        # Should return 400 (rejected) or 200 with empty results
        assert response.status_code in (200, 400)
        if response.status_code == 200:
            # If accepted, should return safe empty result
            data = response.json()
            assert "paths" in data
            # SQL injection should only return paths that match the prefix filter.
            # Since we're using file-based storage (not SQL), the prefix is used
            # as a literal string match. Any returned paths must start with that
            # prefix - this proves no SQL execution occurred (which would return
            # data from "other tables" that don't match the prefix).
            for path in data["paths"]:
                assert path.startswith(payload), (
                    f"Returned path '{path}' does not start with prefix '{payload}' - "
                    "this could indicate SQL injection succeeded"
                )


class TestSQLInjectionTagNames:
    """Test SQL injection attempts in tag names.

    Tag names have stricter validation (alphanumeric + dots/underscores/hyphens).
    These tests verify the validation rejects SQL metacharacters.
    """

    SQL_INJECTION_TAG_PAYLOADS = [
        "'; DROP TABLE tags; --",
        "test' OR '1'='1",
        "latest; DELETE FROM *",
        'v1.0"; DROP TABLE --',
    ]

    @pytest.mark.parametrize("payload", SQL_INJECTION_TAG_PAYLOADS)
    def test_sql_injection_in_tag_name_create(
        self, client: TestClient, test_storage_service: StorageService, payload: str
    ) -> None:
        """SQL injection in tag name creation should be rejected."""
        # First create a valid artifact
        content = b"test content"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}
        upload_response = client.post("/api/v1/upload/security/sqlitest", files=files)
        assert upload_response.status_code == 200

        # Try to create tag with SQL injection payload
        response = client.post(
            "/api/v1/artifacts/security/sqlitest/latest/tags",
            json={"tag_name": payload},
        )

        # Should be rejected by validation (422) or bad request (400)
        assert response.status_code in (400, 422)

    @pytest.mark.parametrize("payload", SQL_INJECTION_TAG_PAYLOADS)
    def test_sql_injection_in_tag_flush(self, client: TestClient, payload: str) -> None:
        """SQL injection in flush tag endpoint should be rejected."""
        response = client.post(
            f"/api/v1/tags/{payload}/flush",
            params={"confirm_walk_filesystem": True},
        )

        # Should be rejected by path validation (422 from regex)
        assert response.status_code == 422


# =============================================================================
# Unicode Attack Tests
# =============================================================================


class TestUnicodeNormalizationAttacks:
    """Test Unicode normalization and homoglyph attacks.

    These tests verify that Unicode characters that look similar to ASCII
    or have special normalization properties cannot be used to bypass
    path validation or cause confusion.
    """

    def test_unicode_path_traversal_blocked(
        self, client: TestClient, test_storage_service: StorageService
    ) -> None:
        """Unicode lookalike dots should not allow path traversal."""
        content = b"test content"
        storage_root = test_storage_service.config.storage_path

        # Try various Unicode dot-like characters that look like ".." but aren't
        # U+2024 = ONE DOT LEADER, U+2025 = TWO DOT LEADER
        unicode_traversal_payloads = [
            ("../\u2024/etc/passwd", "dot leader"),
            ("\u2025/etc/passwd", "two dot leader"),
        ]

        for payload, description in unicode_traversal_payloads:
            files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}
            response = client.post(f"/api/v1/upload/{payload}", files=files)

            # The security property we're testing: Unicode lookalikes should not
            # allow escaping the storage directory. Either:
            # 1. The request is rejected (status != 200), OR
            # 2. If accepted, the stored artifact is within storage_root
            if response.status_code == 200:
                artifact_path = response.json().get("artifact_path", "")
                # Verify the artifact was stored within the storage root
                stored_path = storage_root / artifact_path
                assert stored_path.resolve().is_relative_to(storage_root.resolve()), (
                    f"Unicode traversal with {description} escaped storage root: "
                    f"artifact_path={artifact_path}, resolved to {stored_path.resolve()}"
                )

    def test_unicode_directory_confusion(self, client: TestClient) -> None:
        """Test that Unicode lookalikes don't cause directory confusion."""
        # Upload to a path with normal ASCII
        content = b"original content"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}
        response = client.post("/api/v1/upload/test/unicodetest", files=files)
        assert response.status_code == 200

        # Try to read with Unicode lookalike path - should not find the original
        # Using fullwidth characters that look similar
        response = client.get("/api/v1/artifacts/\uff54\uff45\uff53\uff54/unicodetest")
        # Either rejected or returns empty (not the original content)
        if response.status_code == 200:
            data = response.json()
            # Should be empty, not the original artifact
            assert len(data.get("versions", [])) == 0

    def test_unicode_special_chars_handled_safely(self, client: TestClient) -> None:
        """Unicode special characters should be handled safely."""
        special_chars = [
            "\u202e",  # Right-to-left override
            "\u200b",  # Zero-width space
            "\ufeff",  # Byte order mark
        ]

        for char in special_chars:
            response = client.get(f"/api/v1/artifacts/test{char}path")
            # Should not cause a server error
            assert response.status_code != 500


# =============================================================================
# Symlink Attack Tests
# =============================================================================


class TestSymlinkAttacks:
    """Test symlink-based attacks.

    These tests verify that the system properly rejects symlinks that
    could be used to escape the storage directory or access unauthorized
    files.

    Security mechanism: The artifact_dir_path() function uses Path.resolve()
    to follow symlinks and verifies that the resolved path remains within
    the storage root directory. This protects both uploads and reads.
    """

    def test_symlink_in_artifact_path_rejected(
        self, client: TestClient, test_storage_service: StorageService
    ) -> None:
        """Symlinks in artifact paths should not allow escaping storage root.

        The artifact_dir_path() helper uses resolve() to detect when a path,
        including any symlinks, would escape the storage directory, and rejects
        such paths.
        """
        import shutil
        import tempfile

        storage_base = test_storage_service.config.storage_path

        # Create target directory OUTSIDE the storage root
        # We need a separate temp directory, not a subdirectory of storage_base
        outside_temp = Path(tempfile.mkdtemp(prefix="magpie_symlink_test_outside_"))
        target_path = outside_temp / "escape_target"
        target_path.mkdir(parents=True, exist_ok=True)

        # Create a symlink inside storage pointing outside
        symlink_path = storage_base / "escape_link"

        try:
            symlink_path.symlink_to(target_path)
        except OSError:
            shutil.rmtree(outside_temp, ignore_errors=True)
            pytest.skip("Cannot create symlinks on this system")

        try:
            # Try to upload through the symlink
            content = b"test content"
            files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}

            response = client.post(
                "/api/v1/upload/escape_link/artifact",
                files=files,
            )

            # The upload should be rejected because the symlink escapes storage root
            assert response.status_code == 400, (
                f"Expected 400 for symlink escape attempt, got {response.status_code}: "
                f"{response.text}"
            )
            assert "resolves outside" in response.text.lower()

            # Double-check that nothing was written to the target
            target_artifact_dir = target_path / "artifact"
            assert not (target_artifact_dir / "blobs").exists(), (
                "Upload followed symlink and wrote outside storage!"
            )
        finally:
            # Clean up the symlink and the outside temp directory
            symlink_path.unlink(missing_ok=True)
            shutil.rmtree(outside_temp, ignore_errors=True)

    def test_symlink_read_artifact_rejected(
        self, client: TestClient, test_storage_service: StorageService
    ) -> None:
        """Symlinks should not allow reading files outside storage root.

        Tests that attempting to read artifact info through a symlink that
        escapes the storage directory is rejected.
        """
        import shutil
        import tempfile

        storage_base = test_storage_service.config.storage_path

        # Create target directory OUTSIDE the storage root with some content
        outside_temp = Path(tempfile.mkdtemp(prefix="magpie_symlink_read_test_"))
        target_path = outside_temp / "sensitive_data"
        target_path.mkdir(parents=True, exist_ok=True)

        # Create some "sensitive" content that should NOT be readable
        (target_path / "secret.txt").write_text("sensitive data")

        # Create a symlink inside storage pointing outside
        symlink_path = storage_base / "escape_link"

        try:
            symlink_path.symlink_to(target_path)
        except OSError:
            shutil.rmtree(outside_temp, ignore_errors=True)
            pytest.skip("Cannot create symlinks on this system")

        try:
            # Try to list artifacts through the symlink
            response = client.get("/api/v1/artifacts/escape_link")

            # The request should be rejected because the symlink escapes storage root
            assert response.status_code == 400, (
                f"Expected 400 for symlink escape attempt, got {response.status_code}: "
                f"{response.text}"
            )
            assert "resolves outside" in response.text.lower()
        finally:
            # Clean up the symlink and the outside temp directory
            symlink_path.unlink(missing_ok=True)
            shutil.rmtree(outside_temp, ignore_errors=True)

    def test_symlink_in_nested_path_rejected(
        self, client: TestClient, test_storage_service: StorageService
    ) -> None:
        """Symlinks in nested paths should also be rejected.

        Tests that symlinks anywhere in the artifact path (not just at the root)
        are detected and rejected.
        """
        import shutil
        import tempfile

        storage_base = test_storage_service.config.storage_path

        # Create a legitimate top-level directory
        legit_dir = storage_base / "project"
        legit_dir.mkdir(parents=True, exist_ok=True)

        # Create target directory OUTSIDE the storage root
        outside_temp = Path(tempfile.mkdtemp(prefix="magpie_nested_symlink_test_"))
        target_path = outside_temp / "escape_target"
        target_path.mkdir(parents=True, exist_ok=True)

        # Create a symlink inside the legitimate directory pointing outside
        symlink_path = legit_dir / "evil_link"

        try:
            symlink_path.symlink_to(target_path)
        except OSError:
            shutil.rmtree(outside_temp, ignore_errors=True)
            pytest.skip("Cannot create symlinks on this system")

        try:
            # Try to upload through the nested symlink
            content = b"test content"
            files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}

            response = client.post(
                "/api/v1/upload/project/evil_link/artifact",
                files=files,
            )

            # The upload should be rejected
            assert response.status_code == 400, (
                f"Expected 400 for nested symlink escape, got {response.status_code}: "
                f"{response.text}"
            )
            assert "resolves outside" in response.text.lower()
        finally:
            # Clean up the symlink and the outside temp directory
            symlink_path.unlink(missing_ok=True)
            shutil.rmtree(outside_temp, ignore_errors=True)

    def test_double_dot_path_normalized_by_http(self, client: TestClient) -> None:
        """Double dot (..) in URL path is normalized by HTTP framework.

        The HTTP framework normalizes URLs before they reach the application,
        so 'test/../other' becomes 'other'. This is standard HTTP behavior
        and is secure - the traversal happens within the URL namespace, not
        the filesystem.
        """
        content = b"test content"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}

        # Double dot is normalized by HTTP - test/../other becomes just 'other'
        response = client.post("/api/v1/upload/test/../other", files=files)
        # This succeeds because the URL is normalized to /api/v1/upload/other
        assert response.status_code == 200
        # The artifact_path should be 'other', not 'test/../other'
        assert response.json()["artifact_path"] == "other"


# =============================================================================
# Path Traversal Tests
# =============================================================================


class TestPathTraversal:
    """Test path traversal attacks using ../ patterns.

    Security tests vs unit tests: Unit tests in test_path_validation.py verify
    that individual functions (normalize_artifact_path, validate_artifact_path)
    correctly reject malicious inputs. These security tests verify that the
    complete HTTP request pipeline handles attacks correctly - including HTTP
    framework URL normalization, route parsing, and response handling. Both
    layers matter: unit tests catch bugs in validation logic, while integration
    tests catch misconfigurations (e.g., a route that forgets to call validation).

    Plain '../' in URLs is normalized by the HTTP framework before
    reaching the application, so these tests verify HTTP-level behavior.
    """

    def test_url_path_traversal_normalized_by_http(self, client: TestClient) -> None:
        """Plain ../ in URL is normalized by HTTP framework (safe).

        URLs like '/api/v1/upload/../../../etc/passwd' are normalized by
        the HTTP framework before reaching the application. This is standard
        HTTP behavior and is secure.
        """
        content = b"test content"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}

        # This URL gets normalized by HTTP to /etc/passwd (outside API path)
        # which results in 404 or similar - the framework handles it safely
        response = client.post("/api/v1/upload/../../../etc/passwd", files=files)

        # Either 404 (path outside API) or 200 with normalized path
        # The key is it cannot actually access /etc/passwd
        assert response.status_code in (200, 400, 404)
        if response.status_code == 200:
            # If it succeeded, verify the path was normalized safely
            artifact_path = response.json().get("artifact_path", "")
            assert ".." not in artifact_path

    def test_traversal_inside_path_normalized(self, client: TestClient) -> None:
        """Traversal inside path segment is normalized safely.

        'test/../other' becomes 'other' - the traversal stays within the
        API's URL namespace.
        """
        content = b"test content"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post("/api/v1/upload/test/../otherpath", files=files)
        assert response.status_code == 200
        # The path was normalized to just 'otherpath'
        assert response.json()["artifact_path"] == "otherpath"


# =============================================================================
# Null Byte Injection Tests
# =============================================================================


class TestNullByteInjection:
    """Test null byte injection attacks.

    Null bytes can truncate strings in C-based systems and potentially
    bypass extension checks or path validation. These tests verify that
    null bytes are properly handled.

    Note: Python/FastAPI may handle null bytes differently than C-based
    systems. The key security property is that null bytes cannot be used
    to bypass security controls or access unintended files.
    """

    def test_null_byte_in_tag_name(
        self, client: TestClient, test_storage_service: StorageService
    ) -> None:
        """Null byte in tag name should be rejected."""
        # Create a valid artifact first
        content = b"test content"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}
        upload_response = client.post("/api/v1/upload/nulltest/artifact", files=files)
        assert upload_response.status_code == 200

        # Try to create tag with null byte
        null_payloads = [
            "tag\x00name",
            "\x00tag",
            "tag\x00",
        ]

        for payload in null_payloads:
            response = client.post(
                "/api/v1/artifacts/nulltest/artifact/latest/tags",
                json={"tag_name": payload},
            )
            # Should be rejected
            assert response.status_code in (400, 422), (
                f"Null byte in tag name not rejected: {repr(payload)}"
            )

    def test_null_byte_cannot_bypass_traversal_check(self) -> None:
        """Null byte should not allow bypassing path traversal check.

        This tests that null bytes in paths are handled safely by the
        path validation functions. The HTTP layer itself rejects null bytes
        in URLs (httpx raises InvalidURL for non-printable ASCII), which
        provides defense-in-depth.

        For direct function testing, we verify that normalize_artifact_path
        processes the full string including null bytes, without C-style
        truncation that could bypass traversal checks.
        """
        import httpx

        # Verify HTTP layer rejects null bytes (defense in depth)
        with pytest.raises(httpx.InvalidURL):
            # This confirms null bytes cannot reach the application via HTTP
            httpx.URL("/api/v1/upload/test\x00path")

        # Test that normalize_artifact_path handles null bytes correctly
        # (processes full string, doesn't truncate at null like C strings)
        # If .. appears anywhere in the path (before or after null), it should be rejected
        traversal_with_null = [
            "../etc/passwd\x00.txt",  # Traversal before null
            "test/../\x00secret",  # Traversal before null
        ]

        for payload in traversal_with_null:
            # The traversal should be detected regardless of null bytes
            with pytest.raises(InvalidArtifactPathError):
                normalize_artifact_path(payload)

        # A path with null but no traversal should be processed (null is just a character)
        # Python strings don't truncate at null bytes
        safe_with_null = "safe\x00path"
        result = normalize_artifact_path(safe_with_null)
        # Verify full string was processed (no truncation)
        assert result == safe_with_null


# =============================================================================
# Combined Attack Tests
# =============================================================================


class TestCombinedAttacks:
    """Test combined attack vectors.

    These tests verify that combining multiple attack techniques
    doesn't allow bypassing security controls.
    """

    def test_sql_injection_with_traversal_normalized(self, client: TestClient) -> None:
        """Combined SQL injection and path traversal is safely handled.

        The HTTP framework normalizes the path traversal, and the remaining
        SQL injection characters are harmless for file-based storage.
        """
        content = b"test content"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}

        # The ../ part gets normalized by HTTP, leaving just the SQL part
        payload = "../../../'; DROP TABLE artifacts; --"
        response = client.post(f"/api/v1/upload/{payload}", files=files)

        # Either normalized and stored (safe), or rejected
        # The key is no SQL execution happens
        assert response.status_code in (200, 400, 404)
        if response.status_code == 200:
            # After HTTP normalization removes ../../../, the remaining SQL
            # characters are safely stored as literal path characters.
            # This is secure because file-based storage treats them as plain text.
            data = response.json()
            artifact_path = data.get("artifact_path", "")
            # Verify the SQL injection payload was stored as-is (harmless in file storage)
            assert "DROP" in artifact_path or "'" in artifact_path


# =============================================================================
# Boundary Tests
# =============================================================================


class TestBoundaryConditions:
    """Test boundary conditions and edge cases.

    Note: Basic validation tests (empty paths, whitespace-only paths, reserved
    segments, hidden segments) are covered by unit tests in
    tests/unit/test_path_validation.py. This class focuses on HTTP-level
    integration tests that verify the complete request pipeline handles edge
    cases correctly (not just the validation functions in isolation).
    """

    def test_very_long_path_handled_safely(self, client: TestClient) -> None:
        """Very long paths should be handled without server errors."""
        content = b"test content"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}

        # Create a very long path
        long_segment = "a" * 255
        long_path = "/".join([long_segment] * 10)

        response = client.post(f"/api/v1/upload/{long_path}", files=files)
        # Should either succeed or reject with appropriate error
        # Should not cause server error (500)
        assert response.status_code != 500
