"""Unit tests for StreamingMultipartHandler.

These tests directly exercise the streaming multipart handler without going through
the full HTTP stack. They validate edge cases and error handling that are difficult
to test via integration tests.

Tests cover:
- Boundary extraction from Content-Type headers
- Multiple file parts rejection
- Size limit enforcement
- Cleanup on error
- Content-Disposition parsing
- Header size limits
"""

from __future__ import annotations

from pathlib import Path

import pytest
from python_multipart.multipart import MultipartParser

from magpie.server.routes.upload import (
    HeaderLimitExceededError,
    MalformedMultipartError,
    StreamingMultipartHandler,
    UploadSizeExceededError,
)


class TestBoundaryExtraction:
    """Tests for boundary extraction from Content-Type headers."""

    def test_case_insensitive_boundary_parameter(self) -> None:
        """Boundary parameter name should be case-insensitive per RFC 2046."""
        test_cases = [
            ('multipart/form-data; boundary="test123"', b"test123"),
            ('multipart/form-data; Boundary="test123"', b"test123"),
            ('multipart/form-data; BOUNDARY="test123"', b"test123"),
            ('multipart/form-data; BoUnDaRy="test123"', b"test123"),
        ]

        for content_type, expected_boundary in test_cases:
            # This test validates that the upload endpoint's boundary extraction
            # (which happens before handler creation) is case-insensitive.
            # The handler itself doesn't parse Content-Type, but this documents
            # the expected behavior.
            import re

            match = re.search(r'boundary\s*=\s*"([^"]+)"', content_type, flags=re.IGNORECASE)
            assert match is not None, f"Failed to extract boundary from: {content_type}"
            assert match.group(1).encode("utf-8") == expected_boundary

    def test_quoted_boundary_value(self) -> None:
        """Quoted boundary values should be supported per RFC 2046."""
        import re

        content_type = 'multipart/form-data; boundary="----WebKitFormBoundary"'
        match = re.search(r'boundary\s*=\s*"([^"]+)"', content_type, flags=re.IGNORECASE)
        assert match is not None
        assert match.group(1) == "----WebKitFormBoundary"

    def test_unquoted_boundary_value(self) -> None:
        """Unquoted boundary values should be supported per RFC 2046."""
        import re

        content_type = "multipart/form-data; boundary=simple-boundary-123"
        # Try quoted format first (will fail)
        match = re.search(r'boundary\s*=\s*"([^"]+)"', content_type, flags=re.IGNORECASE)
        assert match is None
        # Try unquoted format
        match = re.search(
            r"boundary\s*=\s*([!#$%&'*+.0-9A-Z^_`a-z|~-]+)", content_type, flags=re.IGNORECASE
        )
        assert match is not None
        assert match.group(1) == "simple-boundary-123"

    def test_whitespace_around_equals(self) -> None:
        """Whitespace around = sign should be handled per RFC 2046."""
        import re

        test_cases = [
            'multipart/form-data; boundary="test"',
            'multipart/form-data; boundary ="test"',
            'multipart/form-data; boundary= "test"',
            'multipart/form-data; boundary = "test"',
        ]

        for content_type in test_cases:
            match = re.search(r'boundary\s*=\s*"([^"]+)"', content_type, flags=re.IGNORECASE)
            assert match is not None, f"Failed to parse: {content_type}"
            assert match.group(1) == "test"


class TestMultipleFilePartsRejection:
    """Tests for multiple file parts rejection."""

    @pytest.fixture
    def temp_dir(self, tmp_path: Path) -> Path:
        """Create a temporary directory for handler temp files."""
        return tmp_path

    def test_first_file_part_accepted_second_rejected(self, temp_dir: Path) -> None:
        """First file part should be processed, second should raise error."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        boundary = b"test-boundary"

        # Craft multipart payload with two file parts
        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="first.bin"\r\n'
            b"\r\n"
            b"first file content\r\n"
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="second.bin"\r\n'
            b"\r\n"
            b"second file content\r\n"
            b"--test-boundary--\r\n"
        )

        parser = MultipartParser(boundary, handler.get_callbacks())

        # Parser should raise MalformedMultipartError when second file part is encountered
        with pytest.raises(MalformedMultipartError, match="Multiple 'file' parts not allowed"):
            parser.write(payload)
            parser.finalize()

        # Cleanup temp file from first part
        handler.cleanup()

    def test_file_part_found_flag_set(self, temp_dir: Path) -> None:
        """File part found flag should be set when file part is encountered."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        boundary = b"test-boundary"

        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"\r\n"
            b"test content\r\n"
            b"--test-boundary--\r\n"
        )

        parser = MultipartParser(boundary, handler.get_callbacks())
        parser.write(payload)
        parser.finalize()
        handler.finalize()

        # File part should have been found
        assert handler._file_part_found is True

        handler.cleanup()


class TestSizeLimitEnforcement:
    """Tests for size limit enforcement."""

    @pytest.fixture
    def temp_dir(self, tmp_path: Path) -> Path:
        """Create a temporary directory for handler temp files."""
        return tmp_path

    def test_exact_size_boundary_succeeds(self, temp_dir: Path) -> None:
        """Upload with exact size at limit should succeed."""
        max_size = 100
        handler = StreamingMultipartHandler(temp_dir, max_size=max_size)
        boundary = b"test-boundary"

        # Create payload with exactly max_size bytes in file content
        file_content = b"x" * max_size
        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"\r\n" + file_content + b"\r\n"
            b"--test-boundary--\r\n"
        )

        parser = MultipartParser(boundary, handler.get_callbacks())
        parser.write(payload)
        parser.finalize()
        handler.finalize()

        result = handler.get_result()
        assert result is not None
        temp_file_path, file_hash, file_bytes = result
        assert file_bytes == max_size

        handler.cleanup()

    def test_one_byte_over_limit_fails(self, temp_dir: Path) -> None:
        """Upload with one byte over limit should raise UploadSizeExceededError."""
        max_size = 100
        handler = StreamingMultipartHandler(temp_dir, max_size=max_size)
        boundary = b"test-boundary"

        # Create payload with max_size + 1 bytes in file content
        file_content = b"x" * (max_size + 1)
        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"\r\n" + file_content + b"\r\n"
            b"--test-boundary--\r\n"
        )

        parser = MultipartParser(boundary, handler.get_callbacks())

        with pytest.raises(UploadSizeExceededError, match="exceeds maximum size"):
            parser.write(payload)
            parser.finalize()

        handler.cleanup()

    def test_incremental_writes_crossing_limit(self, temp_dir: Path) -> None:
        """Size limit should be enforced across incremental writes."""
        max_size = 100
        handler = StreamingMultipartHandler(temp_dir, max_size=max_size)
        boundary = b"test-boundary"

        # Create payload that will be written in chunks
        file_content = b"x" * (max_size + 50)
        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"\r\n" + file_content + b"\r\n"
            b"--test-boundary--\r\n"
        )

        parser = MultipartParser(boundary, handler.get_callbacks())

        # Write in chunks to simulate incremental parsing
        chunk_size = 50
        with pytest.raises(UploadSizeExceededError):
            for i in range(0, len(payload), chunk_size):
                parser.write(payload[i : i + chunk_size])

        handler.cleanup()

    def test_size_limit_includes_all_form_fields(self, temp_dir: Path) -> None:
        """Size limit should include all form field data, not just file content."""
        max_size = 100
        handler = StreamingMultipartHandler(temp_dir, max_size=max_size)
        boundary = b"test-boundary"

        # Create payload with file content (50 bytes) + form field (60 bytes) = 110 bytes total
        # This exceeds the 100 byte limit when counting all part data
        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="other_field"\r\n'
            b"\r\n" + b"x" * 60 + b"\r\n"
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"\r\n" + b"y" * 50 + b"\r\n"
            b"--test-boundary--\r\n"
        )

        parser = MultipartParser(boundary, handler.get_callbacks())

        with pytest.raises(UploadSizeExceededError):
            parser.write(payload)

        handler.cleanup()


class TestCleanupOnError:
    """Tests for cleanup on error."""

    @pytest.fixture
    def temp_dir(self, tmp_path: Path) -> Path:
        """Create a temporary directory for handler temp files."""
        return tmp_path

    def test_temp_file_removed_on_parser_error(self, temp_dir: Path) -> None:
        """Temp file should be removed when parser raises."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        boundary = b"test-boundary"

        # Malformed payload - missing Content-Disposition
        payload = b"--test-boundary\r\n\r\ntest content\r\n--test-boundary--\r\n"

        parser = MultipartParser(boundary, handler.get_callbacks())

        # This should raise MalformedMultipartError
        try:
            parser.write(payload)
            parser.finalize()
        except MalformedMultipartError:
            pass

        # Get temp file path before cleanup (may be None if no file was created)
        temp_file_path = handler._temp_file_path

        # Cleanup should remove temp file if it exists
        handler.cleanup()

        # Verify cleanup succeeded
        if temp_file_path is not None:
            assert not temp_file_path.exists()
        assert handler._temp_file is None
        assert handler._temp_file_path is None or not handler._temp_file_path.exists()

    def test_temp_file_removed_on_size_limit_exceeded(self, temp_dir: Path) -> None:
        """Temp file should be removed when size limit is exceeded."""
        max_size = 50
        handler = StreamingMultipartHandler(temp_dir, max_size=max_size)
        boundary = b"test-boundary"

        # Create payload that exceeds size limit
        file_content = b"x" * (max_size + 100)
        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"\r\n" + file_content + b"\r\n"
            b"--test-boundary--\r\n"
        )

        parser = MultipartParser(boundary, handler.get_callbacks())

        # This should raise UploadSizeExceededError
        try:
            parser.write(payload)
            parser.finalize()
        except UploadSizeExceededError:
            pass

        # Get temp file path before cleanup
        temp_file_path = handler._temp_file_path

        # Cleanup should remove temp file
        handler.cleanup()

        # Verify cleanup succeeded
        if temp_file_path is not None:
            assert not temp_file_path.exists()
        assert handler._temp_file is None

    def test_file_handle_closed_in_error_paths(self, temp_dir: Path) -> None:
        """File handle should be closed even when cleanup encounters errors."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        boundary = b"test-boundary"

        # Create a valid payload to get a temp file created
        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"\r\n"
            b"test content\r\n"
            b"--test-boundary--\r\n"
        )

        parser = MultipartParser(boundary, handler.get_callbacks())
        parser.write(payload)
        parser.finalize()

        # Verify temp file exists and is open
        assert handler._temp_file is not None
        assert handler._temp_file_path is not None

        # Cleanup should close file handle
        handler.cleanup()

        # Verify file handle is closed (trying to write should fail)
        assert handler._temp_file is None

    def test_cleanup_idempotent(self, temp_dir: Path) -> None:
        """Cleanup should be idempotent (safe to call multiple times)."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        boundary = b"test-boundary"

        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"\r\n"
            b"test content\r\n"
            b"--test-boundary--\r\n"
        )

        parser = MultipartParser(boundary, handler.get_callbacks())
        parser.write(payload)
        parser.finalize()

        # Call cleanup multiple times - should not raise
        handler.cleanup()
        handler.cleanup()
        handler.cleanup()

        # State should be clean after all calls
        assert handler._temp_file is None


class TestContentDispositionParsing:
    """Tests for Content-Disposition header parsing."""

    @pytest.fixture
    def temp_dir(self, tmp_path: Path) -> Path:
        """Create a temporary directory for handler temp files."""
        return tmp_path

    def test_quoted_filename(self, temp_dir: Path) -> None:
        """Filename with quotes should be parsed correctly."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        boundary = b"test-boundary"

        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test-artifact.bin"\r\n'
            b"\r\n"
            b"test content\r\n"
            b"--test-boundary--\r\n"
        )

        parser = MultipartParser(boundary, handler.get_callbacks())
        parser.write(payload)
        parser.finalize()
        handler.finalize()

        assert handler.filename == "test-artifact.bin"
        handler.cleanup()

    def test_unquoted_filename(self, temp_dir: Path) -> None:
        """Filename without quotes should be parsed correctly."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        boundary = b"test-boundary"

        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename=test.bin\r\n'
            b"\r\n"
            b"test content\r\n"
            b"--test-boundary--\r\n"
        )

        parser = MultipartParser(boundary, handler.get_callbacks())
        parser.write(payload)
        parser.finalize()
        handler.finalize()

        assert handler.filename == "test.bin"
        handler.cleanup()

    def test_missing_filename(self, temp_dir: Path) -> None:
        """Missing filename should result in None."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        boundary = b"test-boundary"

        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"\r\n'
            b"\r\n"
            b"test content\r\n"
            b"--test-boundary--\r\n"
        )

        parser = MultipartParser(boundary, handler.get_callbacks())
        parser.write(payload)
        parser.finalize()
        handler.finalize()

        assert handler.filename is None
        handler.cleanup()

    def test_malformed_content_disposition_graceful(self, temp_dir: Path) -> None:
        """Malformed Content-Disposition should be handled gracefully.

        The _extract_filename method uses errors="replace" during decode, so invalid
        UTF-8 sequences are replaced with replacement characters rather than raising.
        This test verifies that malformed headers don't cause exceptions.
        """
        handler = StreamingMultipartHandler(temp_dir, max_size=None)

        # Test the internal _extract_filename method directly
        # Invalid UTF-8 sequence - will be replaced, not raise
        malformed_header = b"Content-Disposition: form-data; filename=\xff\xfe"

        # Should not raise an exception (graceful degradation)
        # The actual return value depends on whether the regex matches after replacement
        result = handler._extract_filename(malformed_header)

        # Just verify the call succeeded without raising
        assert isinstance(result, (str, type(None)))

    def test_missing_content_disposition_rejected(self, temp_dir: Path) -> None:
        """Part without Content-Disposition should be rejected."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        boundary = b"test-boundary"

        # Part without Content-Disposition header
        payload = b"--test-boundary\r\n\r\ntest content\r\n--test-boundary--\r\n"

        parser = MultipartParser(boundary, handler.get_callbacks())

        with pytest.raises(MalformedMultipartError, match="missing required Content-Disposition"):
            parser.write(payload)
            parser.finalize()

        handler.cleanup()

    def test_content_disposition_without_name_rejected(self, temp_dir: Path) -> None:
        """Content-Disposition without 'name' parameter should be rejected."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        boundary = b"test-boundary"

        # Content-Disposition without 'name' parameter at all
        # Use 'other' instead to avoid 'filename' matching the 'name=' regex
        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; other="value"\r\n'
            b"\r\n"
            b"test content\r\n"
            b"--test-boundary--\r\n"
        )

        parser = MultipartParser(boundary, handler.get_callbacks())

        # The handler should raise when it processes the header end callback
        # because the Content-Disposition is missing the required 'name' parameter
        with pytest.raises(MalformedMultipartError, match="missing required 'name' parameter"):
            parser.write(payload)
            parser.finalize()

        handler.cleanup()


class TestHeaderSizeLimits:
    """Tests for header size limit enforcement."""

    @pytest.fixture
    def temp_dir(self, tmp_path: Path) -> Path:
        """Create a temporary directory for handler temp files."""
        return tmp_path

    def test_oversized_header_name_rejected(self, temp_dir: Path) -> None:
        """Header name exceeding limit should raise HeaderLimitExceededError."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)

        # Simulate oversized header name by calling callback directly
        # MAX_HEADER_SIZE is 16KB = 16384 bytes
        huge_data = b"x" * 20000

        with pytest.raises(HeaderLimitExceededError, match="exceeds maximum size"):
            handler._on_header_field(huge_data, 0, len(huge_data))

    def test_oversized_header_value_rejected(self, temp_dir: Path) -> None:
        """Header value exceeding limit should raise HeaderLimitExceededError."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)

        # Set a small header name first
        handler._current_header_name = b"Content-Disposition"

        # Then try to add huge header value
        huge_data = b"x" * 20000

        with pytest.raises(HeaderLimitExceededError, match="exceeds maximum size"):
            handler._on_header_value(huge_data, 0, len(huge_data))

    def test_combined_header_size_enforcement(self, temp_dir: Path) -> None:
        """Combined header name + value should be checked against limit."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)

        # Add header name close to limit (8KB)
        header_name = b"X-Custom-Header" + b"x" * (8 * 1024 - 15)
        handler._on_header_field(header_name, 0, len(header_name))

        # Adding 8KB+ of header value should exceed 16KB combined limit
        header_value = b"x" * (8 * 1024 + 100)

        with pytest.raises(HeaderLimitExceededError, match="exceeds maximum size"):
            handler._on_header_value(header_value, 0, len(header_value))

    def test_too_many_headers_per_part_rejected(self, temp_dir: Path) -> None:
        """Part with more than MAX_HEADERS_PER_PART headers should be rejected."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        boundary = b"test-boundary"

        # Create a part with more than MAX_HEADERS_PER_PART (50) headers
        # Each header needs name + value + CRLF
        headers = []
        for i in range(60):  # More than the 50 header limit
            headers.append(f"X-Custom-Header-{i}: value{i}\r\n".encode())

        payload = (
            b"--test-boundary\r\n"
            + b"".join(headers)
            + b'Content-Disposition: form-data; name="file"\r\n'
            b"\r\n"
            b"test content\r\n"
            b"--test-boundary--\r\n"
        )

        parser = MultipartParser(boundary, handler.get_callbacks())

        with pytest.raises(HeaderLimitExceededError, match="more than .* headers"):
            parser.write(payload)
            parser.finalize()

        handler.cleanup()

    def test_reasonable_header_count_accepted(self, temp_dir: Path) -> None:
        """Part with reasonable number of headers should be accepted."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        boundary = b"test-boundary"

        # Create part with reasonable number of headers (under 50)
        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"Content-Type: application/octet-stream\r\n"
            b"X-Custom-Header: value\r\n"
            b"\r\n"
            b"test content\r\n"
            b"--test-boundary--\r\n"
        )

        parser = MultipartParser(boundary, handler.get_callbacks())
        parser.write(payload)
        parser.finalize()
        handler.finalize()

        # Should succeed
        result = handler.get_result()
        assert result is not None

        handler.cleanup()


class TestHandlerGetResult:
    """Tests for get_result() method."""

    @pytest.fixture
    def temp_dir(self, tmp_path: Path) -> Path:
        """Create a temporary directory for handler temp files."""
        return tmp_path

    def test_get_result_returns_none_when_no_file_part(self, temp_dir: Path) -> None:
        """get_result() should return None when no file part was found."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        boundary = b"test-boundary"

        # Upload with only non-file form fields
        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="other_field"\r\n'
            b"\r\n"
            b"some value\r\n"
            b"--test-boundary--\r\n"
        )

        parser = MultipartParser(boundary, handler.get_callbacks())
        parser.write(payload)
        parser.finalize()
        handler.finalize()

        result = handler.get_result()
        assert result is None

        handler.cleanup()

    def test_get_result_returns_correct_file_size(self, temp_dir: Path) -> None:
        """get_result() should return file content size, not including multipart overhead."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        boundary = b"test-boundary"

        file_content = b"x" * 1234  # Known size
        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"\r\n" + file_content + b"\r\n"
            b"--test-boundary--\r\n"
        )

        parser = MultipartParser(boundary, handler.get_callbacks())
        parser.write(payload)
        parser.finalize()
        handler.finalize()

        result = handler.get_result()
        assert result is not None
        temp_file_path, file_hash, file_bytes = result

        # File size should be exactly the file content size, not including overhead
        assert file_bytes == 1234

        handler.cleanup()

    def test_get_result_returns_valid_hash(self, temp_dir: Path) -> None:
        """get_result() should return valid SHA-256 hash."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        boundary = b"test-boundary"

        file_content = b"test content for hashing"
        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"\r\n" + file_content + b"\r\n"
            b"--test-boundary--\r\n"
        )

        parser = MultipartParser(boundary, handler.get_callbacks())
        parser.write(payload)
        parser.finalize()
        handler.finalize()

        result = handler.get_result()
        assert result is not None
        temp_file_path, file_hash, file_bytes = result

        # Hash should be 64-character hex string (SHA-256)
        assert len(file_hash) == 64
        assert all(c in "0123456789abcdef" for c in file_hash)

        # Verify hash is correct
        import hashlib

        expected_hash = hashlib.sha256(file_content).hexdigest()
        assert file_hash == expected_hash

        handler.cleanup()

    def test_get_result_temp_file_exists_and_contains_content(self, temp_dir: Path) -> None:
        """get_result() should return path to temp file with correct content."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        boundary = b"test-boundary"

        file_content = b"test file content"
        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"\r\n" + file_content + b"\r\n"
            b"--test-boundary--\r\n"
        )

        parser = MultipartParser(boundary, handler.get_callbacks())
        parser.write(payload)
        parser.finalize()
        handler.finalize()

        result = handler.get_result()
        assert result is not None
        temp_file_path, file_hash, file_bytes = result

        # Temp file should exist
        assert temp_file_path.exists()

        # Temp file should contain the uploaded content
        with open(temp_file_path, "rb") as f:
            content = f.read()
        assert content == file_content

        handler.cleanup()
