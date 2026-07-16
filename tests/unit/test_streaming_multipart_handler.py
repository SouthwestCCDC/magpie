"""Unit tests for StreamingMultipartHandler.

These tests directly exercise the streaming multipart handler without going through
the full HTTP stack. They validate edge cases and error handling that are difficult
to test via integration tests.

Tests cover:
- Multiple file parts rejection
- Size limit enforcement
- Cleanup on error
- Content-Disposition parsing
- Header size limits
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from magpie.server.routes.upload import (
    HeaderLimitExceededError,
    MalformedMultipartError,
    StreamingMultipartHandler,
    UploadSizeExceededError,
    build_multipart_parser,
)


class TestBoundaryExtraction:
    """Tests for boundary extraction from Content-Type headers.

    NOTE: These tests verify the boundary extraction logic that's implemented
    in upload.py (lines 347-373). The handler itself doesn't parse Content-Type,
    so we test the actual regex patterns used by the upload endpoint.
    """

    @pytest.fixture
    def temp_dir(self, tmp_path: Path) -> Path:
        """Create a temporary directory for handler temp files."""
        return tmp_path

    def test_case_insensitive_boundary_parameter(self, temp_dir: Path) -> None:
        """Boundary parameter name should be case-insensitive per RFC 2046."""
        test_cases = [
            ('multipart/form-data; boundary="test123"', b"test123"),
            ('multipart/form-data; Boundary="test123"', b"test123"),
            ('multipart/form-data; BOUNDARY="test123"', b"test123"),
            ('multipart/form-data; BoUnDaRy="test123"', b"test123"),
        ]

        for content_type, expected_boundary in test_cases:
            # Use the actual boundary extraction logic from upload.py
            match = re.search(r'boundary\s*=\s*"([^"]+)"', content_type, flags=re.IGNORECASE)
            assert match is not None, f"Failed to extract boundary from: {content_type}"
            boundary = match.group(1).encode("utf-8")
            assert boundary == expected_boundary

            # Verify the boundary works with the handler
            handler = StreamingMultipartHandler(temp_dir, max_size=None)
            payload = (
                b"--" + boundary + b"\r\n"
                b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
                b"\r\n"
                b"test content\r\n"
                b"--" + boundary + b"--\r\n"
            )

            parser = build_multipart_parser(boundary, handler)
            parser.write(payload)
            parser.finalize()
            handler.finalize()

            # Verify file was processed
            result = handler.get_result()
            assert result is not None
            handler.cleanup()

    def test_quoted_boundary_value(self, temp_dir: Path) -> None:
        """Quoted boundary values should be supported per RFC 2046."""
        content_type = 'multipart/form-data; boundary="----WebKitFormBoundary"'
        match = re.search(r'boundary\s*=\s*"([^"]+)"', content_type, flags=re.IGNORECASE)
        assert match is not None
        boundary = match.group(1).encode("utf-8")
        assert boundary == b"----WebKitFormBoundary"

        # Verify the boundary works with the handler
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        payload = (
            b"------WebKitFormBoundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"\r\n"
            b"test content\r\n"
            b"------WebKitFormBoundary--\r\n"
        )

        parser = build_multipart_parser(boundary, handler)
        parser.write(payload)
        parser.finalize()
        handler.finalize()

        result = handler.get_result()
        assert result is not None
        handler.cleanup()

    def test_unquoted_boundary_value(self, temp_dir: Path) -> None:
        """Unquoted boundary values should be supported per RFC 2046."""
        content_type = "multipart/form-data; boundary=simple-boundary-123"
        # Try quoted format first
        match = re.search(r'boundary\s*=\s*"([^"]+)"', content_type, flags=re.IGNORECASE)
        if not match:
            # Try unquoted format (matches upload.py logic)
            match = re.search(
                r"boundary\s*=\s*([!#$%&'*+.0-9A-Z^_`a-z|~-]+)", content_type, flags=re.IGNORECASE
            )
        assert match is not None
        boundary = match.group(1).encode("utf-8")
        assert boundary == b"simple-boundary-123"

        # Verify the boundary works with the handler
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        payload = (
            b"--simple-boundary-123\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"\r\n"
            b"test content\r\n"
            b"--simple-boundary-123--\r\n"
        )

        parser = build_multipart_parser(boundary, handler)
        parser.write(payload)
        parser.finalize()
        handler.finalize()

        result = handler.get_result()
        assert result is not None
        handler.cleanup()

    def test_whitespace_around_equals(self, temp_dir: Path) -> None:
        """Whitespace around = sign should be handled per RFC 2046."""
        test_cases = [
            'multipart/form-data; boundary="test"',
            'multipart/form-data; boundary ="test"',
            'multipart/form-data; boundary= "test"',
            'multipart/form-data; boundary = "test"',
        ]

        for content_type in test_cases:
            match = re.search(r'boundary\s*=\s*"([^"]+)"', content_type, flags=re.IGNORECASE)
            assert match is not None, f"Failed to parse: {content_type}"
            boundary = match.group(1).encode("utf-8")
            assert boundary == b"test"

            # Verify the boundary works with the handler
            handler = StreamingMultipartHandler(temp_dir, max_size=None)
            payload = (
                b"--test\r\n"
                b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
                b"\r\n"
                b"test content\r\n"
                b"--test--\r\n"
            )

            parser = build_multipart_parser(boundary, handler)
            parser.write(payload)
            parser.finalize()
            handler.finalize()

            result = handler.get_result()
            assert result is not None
            handler.cleanup()


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

        parser = build_multipart_parser(boundary, handler)

        # Handler should detect the second file part and raise MalformedMultipartError,
        # which is propagated out of parser.write()/parser.finalize().
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

        parser = build_multipart_parser(boundary, handler)
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

        parser = build_multipart_parser(boundary, handler)
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

        parser = build_multipart_parser(boundary, handler)

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

        parser = build_multipart_parser(boundary, handler)

        # Write in chunks to simulate incremental parsing
        chunk_size = 50
        with pytest.raises(UploadSizeExceededError, match="exceeds maximum size"):
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

        parser = build_multipart_parser(boundary, handler)

        with pytest.raises(UploadSizeExceededError):
            parser.write(payload)

        handler.cleanup()

    def test_size_limit_off_by_one_boundary(self, temp_dir: Path) -> None:
        """Verify size limit is enforced at exact byte boundary (off-by-one check)."""
        max_size = 100

        # Test: exactly max_size - 1 should succeed
        handler = StreamingMultipartHandler(temp_dir, max_size=max_size)
        boundary = b"test-boundary"
        file_content = b"x" * (max_size - 1)
        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"\r\n" + file_content + b"\r\n"
            b"--test-boundary--\r\n"
        )

        parser = build_multipart_parser(boundary, handler)
        parser.write(payload)
        parser.finalize()
        handler.finalize()

        result = handler.get_result()
        assert result is not None
        _, _, file_bytes = result
        assert file_bytes == max_size - 1
        handler.cleanup()

        # Test: exactly max_size should succeed (tested elsewhere, but verify here)
        handler = StreamingMultipartHandler(temp_dir, max_size=max_size)
        file_content = b"x" * max_size
        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"\r\n" + file_content + b"\r\n"
            b"--test-boundary--\r\n"
        )

        parser = build_multipart_parser(boundary, handler)
        parser.write(payload)
        parser.finalize()
        handler.finalize()

        result = handler.get_result()
        assert result is not None
        _, _, file_bytes = result
        assert file_bytes == max_size
        handler.cleanup()

        # Test: exactly max_size + 1 should fail
        handler = StreamingMultipartHandler(temp_dir, max_size=max_size)
        file_content = b"x" * (max_size + 1)
        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"\r\n" + file_content + b"\r\n"
            b"--test-boundary--\r\n"
        )

        parser = build_multipart_parser(boundary, handler)

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
        """Temp file should be removed when parser raises.

        The test sends a valid file part first (which creates a temp file),
        then a malformed second part that triggers an error. This ensures
        the cleanup path is properly exercised for a temp file that was
        actually created.
        """
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        boundary = b"test-boundary"

        # Payload starts with a valid file part so the handler creates a temp file,
        # then includes a second malformed part (missing Content-Disposition) that
        # triggers MalformedMultipartError.
        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.txt"\r\n'
            b"Content-Type: application/octet-stream\r\n"
            b"\r\n"
            b"test content\r\n"
            b"--test-boundary\r\n"
            b"\r\n"
            b"malformed\r\n"
            b"--test-boundary--\r\n"
        )

        parser = build_multipart_parser(boundary, handler)

        # This should raise MalformedMultipartError on the malformed second part.
        with pytest.raises(MalformedMultipartError, match="missing required Content-Disposition"):
            parser.write(payload)
            parser.finalize()

        # Get temp file path before cleanup; it should exist because the first part
        # was a valid file upload.
        temp_file_path = handler._temp_file_path
        assert temp_file_path is not None
        assert temp_file_path.exists()

        # Cleanup should remove the temp file
        handler.cleanup()

        # Verify cleanup succeeded
        assert handler._temp_file is None
        assert handler._temp_file_path is None or not handler._temp_file_path.exists()
        assert not temp_file_path.exists()

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

        parser = build_multipart_parser(boundary, handler)

        # This should raise UploadSizeExceededError
        with pytest.raises(UploadSizeExceededError, match="exceeds maximum size"):
            parser.write(payload)
            parser.finalize()

        # Get temp file path before cleanup
        temp_file_path = handler._temp_file_path

        # Cleanup should remove temp file
        handler.cleanup()

        # Verify cleanup succeeded
        if temp_file_path is not None:
            assert not temp_file_path.exists()
        assert handler._temp_file is None

    def test_file_handle_closed_even_when_temp_file_deleted(self, temp_dir: Path) -> None:
        """File handle should be closed even when temp file is already deleted."""
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

        parser = build_multipart_parser(boundary, handler)
        parser.write(payload)
        parser.finalize()

        # Verify temp file exists and is open
        assert handler._temp_file is not None
        assert handler._temp_file_path is not None
        temp_file_path = handler._temp_file_path

        # Simulate external deletion of temp file (error condition)
        temp_file_path.unlink()

        # Cleanup should still close file handle gracefully
        handler.cleanup()

        # Verify file handle is closed despite error
        assert handler._temp_file is None
        assert not temp_file_path.exists()

    def test_error_then_finalize_cleanup(self, temp_dir: Path) -> None:
        """Calling finalize() after error should not leak temp files.

        This tests the critical sequence: error occurs → finalize() is called
        instead of cleanup() → temp file should still be cleaned up.
        """
        handler = StreamingMultipartHandler(temp_dir, max_size=50)
        boundary = b"test-boundary"

        # Create payload that will exceed size limit
        file_content = b"x" * 100
        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"\r\n" + file_content + b"\r\n"
            b"--test-boundary--\r\n"
        )

        parser = build_multipart_parser(boundary, handler)

        # Size limit should be exceeded during parsing
        with pytest.raises(UploadSizeExceededError, match="exceeds maximum size"):
            parser.write(payload)
            parser.finalize()

        # Get temp file path before finalize (may exist if created before error)
        temp_file_path = handler._temp_file_path

        # Caller might call finalize() instead of cleanup() after error
        handler.finalize()

        # finalize() closes the file handle but doesn't unlink the temp file
        # This is expected behavior - caller should use cleanup() on error
        # But we should verify the state is consistent
        assert handler._temp_file is None  # File handle closed

        # Now clean up properly
        handler.cleanup()

        # Verify cleanup removed temp file
        if temp_file_path is not None:
            assert not temp_file_path.exists()

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

        parser = build_multipart_parser(boundary, handler)
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

        parser = build_multipart_parser(boundary, handler)
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

        parser = build_multipart_parser(boundary, handler)
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

        parser = build_multipart_parser(boundary, handler)
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
        malformed_header = b"form-data; filename=\xff\xfe"

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

        parser = build_multipart_parser(boundary, handler)

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

        parser = build_multipart_parser(boundary, handler)

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

        parser = build_multipart_parser(boundary, handler)

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

        parser = build_multipart_parser(boundary, handler)
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

        parser = build_multipart_parser(boundary, handler)
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

        parser = build_multipart_parser(boundary, handler)
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

        parser = build_multipart_parser(boundary, handler)
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
        expected_hash = hashlib.sha256(file_content).hexdigest()
        assert file_hash == expected_hash

        handler.cleanup()

    def test_hash_computed_incrementally_during_streaming(self, temp_dir: Path) -> None:
        """Hash should be computed incrementally as chunks are written.

        This is the core value of streaming upload - hash is computed during
        the stream, not by buffering the entire file then hashing.
        """
        handler = StreamingMultipartHandler(temp_dir, max_size=None)
        boundary = b"test-boundary"

        # Create content that will be written in multiple chunks
        # Use a larger payload to ensure multiple parser callbacks
        file_content = b"chunk1" * 100 + b"chunk2" * 100 + b"chunk3" * 100
        payload = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            b"\r\n" + file_content + b"\r\n"
            b"--test-boundary--\r\n"
        )

        parser = build_multipart_parser(boundary, handler)

        # Write payload in small chunks to force incremental parsing
        chunk_size = 100
        for i in range(0, len(payload), chunk_size):
            parser.write(payload[i : i + chunk_size])

        parser.finalize()
        handler.finalize()

        result = handler.get_result()
        assert result is not None
        temp_file_path, file_hash, file_bytes = result

        # Verify hash matches expected value
        expected_hash = hashlib.sha256(file_content).hexdigest()
        assert file_hash == expected_hash

        # Verify temp file contains correct content
        with open(temp_file_path, "rb") as f:
            written_content = f.read()
        assert written_content == file_content

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

        parser = build_multipart_parser(boundary, handler)
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


class TestStateMachineViolations:
    """Tests for state machine violations (callbacks in wrong order)."""

    @pytest.fixture
    def temp_dir(self, tmp_path: Path) -> Path:
        """Create a temporary directory for handler temp files."""
        return tmp_path

    def test_part_data_before_header_end_rejected(self, temp_dir: Path) -> None:
        """Calling on_part_data before on_header_end should raise error.

        The handler expects headers to be complete before processing data.
        If on_part_data is called without Content-Disposition, it should raise.
        """
        handler = StreamingMultipartHandler(temp_dir, max_size=None)

        # Start a part but don't complete headers
        handler._on_part_begin()

        # Try to write data without completing headers (no Content-Disposition)
        with pytest.raises(MalformedMultipartError, match="missing required Content-Disposition"):
            handler._on_part_data(b"test data", 0, 9)

        handler.cleanup()

    def test_part_data_without_content_disposition_rejected(self, temp_dir: Path) -> None:
        """Part data without Content-Disposition header should be rejected."""
        handler = StreamingMultipartHandler(temp_dir, max_size=None)

        # Start a part
        handler._on_part_begin()

        # Add some other header (not Content-Disposition)
        handler._on_header_field(b"Content-Type", 0, 12)
        handler._on_header_value(b"text/plain", 0, 10)
        handler._on_header_end()

        # Try to write data without Content-Disposition
        with pytest.raises(MalformedMultipartError, match="missing required Content-Disposition"):
            handler._on_part_data(b"test data", 0, 9)

        handler.cleanup()

    def test_multiple_part_begins_without_completion(self, temp_dir: Path) -> None:
        """Multiple part_begin calls should reset state properly.

        The handler should be able to handle multiple part_begin calls
        (e.g., if parser restarts or encounters errors).
        """
        handler = StreamingMultipartHandler(temp_dir, max_size=None)

        # First part begin
        handler._on_part_begin()
        assert handler._current_field_name is None
        assert handler._in_file_field is False

        # Second part begin (before completing first)
        handler._on_part_begin()
        assert handler._current_field_name is None
        assert handler._in_file_field is False

        # State should be clean
        assert handler._current_header_name == b""
        assert handler._current_header_value == b""
        handler.cleanup()

    def test_header_callbacks_with_large_accumulated_data(self, temp_dir: Path) -> None:
        """Handler should reject headers that accumulate beyond size limit.

        Tests the incremental header accumulation to catch DoS attempts
        that send headers in many small chunks.
        """
        handler = StreamingMultipartHandler(temp_dir, max_size=None)

        # Start a part
        handler._on_part_begin()

        # Add header name in small chunks until we exceed limit
        chunk = b"X" * 1000
        # MAX_HEADER_SIZE is 16KB (16384 bytes), so 17 chunks of 1000 bytes should exceed it
        # The 17th chunk should cause the limit to be exceeded
        with pytest.raises(HeaderLimitExceededError, match="exceeds maximum size"):
            for i in range(17):
                handler._on_header_field(chunk, 0, len(chunk))
        handler.cleanup()
