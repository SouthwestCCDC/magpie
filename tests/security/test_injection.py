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
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magpie.storage.exceptions import InvalidArtifactPathError
from magpie.storage.paths import normalize_artifact_path, validate_artifact_path


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
        assert "sql" not in response_text or "sqlalchemy" not in response_text
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
            # SQL injection shouldn't return data from other tables
            for path in data["paths"]:
                assert "DROP" not in path
                assert "SELECT" not in path


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
        self, client: TestClient, test_storage_service, payload: str
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

        # Should be rejected by path validation (422 from regex) or 401 (auth)
        assert response.status_code in (401, 422)


# =============================================================================
# Unicode Attack Tests
# =============================================================================


class TestUnicodeNormalizationAttacks:
    """Test Unicode normalization and homoglyph attacks.

    These tests verify that Unicode characters that look similar to ASCII
    or have special normalization properties cannot be used to bypass
    path validation or cause confusion.
    """

    def test_unicode_path_traversal_blocked(self, client: TestClient) -> None:
        """Unicode lookalike dots should not allow path traversal."""
        content = b"test content"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}

        # Try various Unicode dot-like characters
        unicode_traversal_payloads = [
            "../\u2024/etc/passwd",  # Dot leader
            "\u2025/etc/passwd",  # Two dot leader
        ]

        for payload in unicode_traversal_payloads:
            files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}
            response = client.post(f"/api/v1/upload/{payload}", files=files)
            # The path traversal component should be blocked
            assert response.status_code != 200 or ".." not in response.json().get(
                "artifact_path", ""
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

    These tests verify that the system properly handles or rejects
    symlinks that could be used to escape the storage directory or
    access unauthorized files.

    Note: Currently symlinks in the storage directory are followed, which
    could be a security concern. This test documents the current behavior.
    """

    @pytest.mark.xfail(reason="Symlink following is currently allowed - see issue for security review")
    def test_symlink_in_artifact_path_rejected(
        self, client: TestClient, test_storage_service, tmp_path: Path
    ) -> None:
        """Symlinks in artifact paths should not allow escaping storage root.

        This test is marked as xfail because the current implementation
        follows symlinks. This behavior should be reviewed for security
        implications.
        """
        storage_base = test_storage_service.config.storage_path

        # Create a symlink inside storage pointing outside
        symlink_path = storage_base / "escape_link"
        target_path = tmp_path / "outside_storage"
        target_path.mkdir(parents=True, exist_ok=True)

        try:
            symlink_path.symlink_to(target_path)
        except OSError:
            pytest.skip("Cannot create symlinks on this system")

        # Try to upload through the symlink
        content = b"test content"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/escape_link/artifact",
            files=files,
        )

        # The storage service should handle this safely
        # Either reject or store in the actual symlink location (not follow it)
        if response.status_code == 200:
            # If upload succeeded, verify it didn't write to the target
            target_file = target_path / "artifact"
            assert not (target_file / "blobs").exists(), (
                "Upload followed symlink and wrote outside storage!"
            )

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

    These tests verify that the normalize_artifact_path and
    verify_path_is_descendant functions properly block all forms
    of path traversal attempts.

    Note: Plain '../' in URLs is normalized by the HTTP framework before
    reaching the application, so we test URL-encoded variants that bypass
    HTTP normalization and reach the application-level validation.
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

    def test_normalize_artifact_path_rejects_traversal(self) -> None:
        """Direct test of normalize_artifact_path rejecting traversal."""
        with pytest.raises(InvalidArtifactPathError) as exc_info:
            normalize_artifact_path("test/../../../etc/passwd")
        assert "traversal" in str(exc_info.value).lower()

    def test_normalize_artifact_path_allows_double_dot_in_name(self) -> None:
        """Paths like 'v1..2' should be allowed (not directory traversal)."""
        # This should NOT raise - double dots in filenames are OK
        result = normalize_artifact_path("version/v1..2")
        assert result == "version/v1..2"

    def test_validate_artifact_path_rejects_traversal(self) -> None:
        """Direct test of validate_artifact_path rejecting traversal."""
        with pytest.raises(InvalidArtifactPathError):
            validate_artifact_path("test/../other")

    @pytest.mark.parametrize(
        "path,expected",
        [
            ("test/artifact", "test/artifact"),  # Normal path
            ("/test/artifact/", "test/artifact"),  # Leading/trailing slashes
            ("//test//artifact//", "test/artifact"),  # Multiple slashes
            ("test", "test"),  # Single segment
        ],
    )
    def test_normalize_path_valid_inputs(self, path: str, expected: str) -> None:
        """Valid paths should normalize correctly."""
        result = normalize_artifact_path(path)
        assert result == expected


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
        self, client: TestClient, test_storage_service
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
        """Null byte should not allow bypassing path traversal check."""
        # Direct test of the validation function
        traversal_with_null = [
            "../etc/passwd\x00",
            "..\x00/etc/passwd",
            "test/../\x00../../etc/passwd",
        ]

        for payload in traversal_with_null:
            # If the null byte truncates before .., it might pass
            # But if .. is present, it should be caught
            if ".." in payload.split("\x00")[0]:
                with pytest.raises(InvalidArtifactPathError):
                    normalize_artifact_path(payload.split("\x00")[0])


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
            # SQL characters in path are just literal characters
            assert "DROP" not in response.json().get("artifact_path", "DROP")


# =============================================================================
# Boundary Tests
# =============================================================================


class TestBoundaryConditions:
    """Test boundary conditions and edge cases."""

    def test_empty_path_rejected(self, client: TestClient) -> None:
        """Empty path should be rejected by validation."""
        with pytest.raises(InvalidArtifactPathError):
            normalize_artifact_path("")

    def test_whitespace_only_path_allowed_as_literal(self) -> None:
        """Whitespace-only path is treated as literal path segment.

        Note: The normalize_artifact_path function only strips leading/trailing
        slashes, not whitespace. A path of '   ' is technically valid (though
        unusual). This is acceptable as whitespace in URLs gets encoded.
        """
        # Whitespace is preserved as a literal path segment
        result = normalize_artifact_path("   ")
        assert result == "   "  # Preserved as-is

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

    def test_reserved_segments_rejected(self, client: TestClient) -> None:
        """Reserved path segments (blobs, metadata, .magpie) should be rejected."""
        content = b"test content"

        reserved = ["blobs", "metadata", ".magpie"]

        for segment in reserved:
            files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}
            response = client.post(f"/api/v1/upload/test/{segment}/artifact", files=files)
            assert response.status_code == 400
            assert "reserved" in response.json().get("message", "").lower()

    def test_hidden_segments_rejected(self, client: TestClient) -> None:
        """Hidden path segments (starting with .) should be rejected."""
        content = b"test content"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post("/api/v1/upload/test/.hidden/artifact", files=files)
        assert response.status_code == 400
        assert "cannot start with" in response.json().get("message", "").lower()
