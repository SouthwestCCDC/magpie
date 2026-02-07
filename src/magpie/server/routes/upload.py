"""Upload endpoint for storing artifacts."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from pathlib import Path
from typing import IO, Annotated

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel
from python_multipart.multipart import MultipartParser

from magpie.config import MagpieSettings
from magpie.server.deps import get_magpie_settings, get_storage_service, require_write_scope
from magpie.storage.exceptions import InvalidArtifactPathError
from magpie.storage.paths import normalize_artifact_path
from magpie.storage.service import StorageService

logger = structlog.get_logger()

router = APIRouter()


class UploadSizeExceededError(Exception):
    """Raised when upload exceeds configured max_upload_size."""


class SizeLimitedReader:
    """Wrapper around a file stream that enforces a maximum size limit.

    This wrapper tracks bytes read from the underlying stream and raises
    UploadSizeExceededError if the limit is exceeded during streaming.

    SECURITY: The _high_water_mark tracks the maximum position ever reached,
    preventing bypass via seek operations. Even if a caller seeks backward
    and re-reads data, _high_water_mark ensures we never allow reading
    beyond max_size bytes from the start of the stream.
    """

    def __init__(self, stream: IO[bytes], max_size: int | None = None) -> None:
        """Initialize size-limited reader.

        Args:
            stream: Underlying file stream to wrap.
            max_size: Maximum allowed bytes to read (None = no limit).
                     When None, reader tracks bytes but doesn't enforce a limit.
                     This is backward compatible - existing callers that pass
                     a concrete max_size continue to work unchanged.
        """
        self._stream = stream
        self._max_size = max_size
        self._bytes_read = 0
        self._high_water_mark = 0  # Maximum position ever reached

    @property
    def bytes_read(self) -> int:
        """Get total bytes read from stream."""
        return self._high_water_mark

    def read(self, size: int = -1) -> bytes:
        """Read from stream, enforcing size limit.

        Args:
            size: Number of bytes to read (-1 for all).

        Returns:
            Bytes read from stream.

        Raises:
            UploadSizeExceededError: If cumulative read exceeds max_size.

        NOTE: The check happens AFTER reading to simplify the code. This means we may
        temporarily hold up to one chunk more than max_size in memory before raising.
        This is acceptable because:
        1. The exceeded bytes are never persisted - the exception aborts processing
        2. The overage is bounded by the read chunk size (typically 8KB-64KB)
        3. Pre-checking when size=-1 is impossible (we don't know how much will be read)
        4. Memory is already allocated by the underlying stream.read() call regardless

        SECURITY: We track both current position (_bytes_read) and the maximum position
        ever reached (_high_water_mark). The limit is enforced against _high_water_mark,
        preventing bypass via seek operations that try to re-read data.
        """
        data = self._stream.read(size)
        self._bytes_read += len(data)
        # Update high water mark - this never decreases, preventing seek bypass attacks
        self._high_water_mark = max(self._high_water_mark, self._bytes_read)
        if self._max_size is not None and self._high_water_mark > self._max_size:
            # NOTE: Revealing the exact limit is intentional - it helps legitimate users
            # understand the constraint. The limit is not security-sensitive information;
            # it's a configuration value that would be documented anyway.
            raise UploadSizeExceededError(f"Upload exceeds maximum size of {self._max_size} bytes")
        return data

    def seek(self, pos: int, whence: int = 0) -> int:
        """Seek in stream and update bytes_read counter appropriately.

        SECURITY: The _high_water_mark is never decreased by seek operations,
        preventing bypass attacks. Even if a caller seeks backward (or uses
        SEEK_END then SEEK_SET to return to the start), they cannot read more
        than max_size bytes from the beginning of the stream.

        For all seek modes, we use the resulting absolute position from the
        underlying stream's seek() to track _bytes_read. The _high_water_mark
        preserves the maximum position ever reached.
        """
        result = self._stream.seek(pos, whence)
        # Update _bytes_read to current position; _high_water_mark is preserved
        # and only updated in read() when we actually advance past it
        self._bytes_read = result
        return result

    def tell(self) -> int:
        """Get current position in stream (pass-through)."""
        return self._stream.tell()


class StreamingMultipartHandler:
    """Handler for streaming multipart/form-data without buffering.

    This class uses python-multipart's streaming parser to extract file content
    directly from the request stream, writing chunks directly to a temp file on
    the data volume while computing the SHA-256 hash incrementally. This achieves
    true single-pass streaming: network → parse → hash → temp file.
    """

    def __init__(self, boundary: bytes, temp_path: Path, max_size: int | None = None) -> None:
        """Initialize streaming multipart handler.

        Args:
            boundary: Multipart boundary from Content-Type header.
            temp_path: Directory for temporary files (on data volume).
            max_size: Maximum allowed upload size (for early rejection).
        """
        self._boundary = boundary
        self._temp_path = temp_path
        self._max_size = max_size
        self._temp_file_fd: int | None = None
        self._temp_file_path: Path | None = None
        self._hasher = hashlib.sha256()
        self._current_field_name: str | None = None
        self._current_header_name: bytes = b""
        self._current_header_value: bytes = b""
        self._filename: str | None = None
        self._in_file_field = False
        self._total_bytes = 0
        self._file_part_found = False

    def _extract_filename(self, content_disposition: bytes) -> str | None:
        """Extract filename from Content-Disposition header."""
        # Parse: Content-Disposition: form-data; name="file"; filename="artifact.bin"
        try:
            cd_str = content_disposition.decode("utf-8", errors="replace")
            # Look for filename="..." or filename*=...
            match = re.search(r'filename="([^"]+)"', cd_str)
            if match:
                return match.group(1)
            match = re.search(r"filename=([^;\s]+)", cd_str)
            if match:
                return match.group(1)
        except (UnicodeDecodeError, re.error):  # nosec B110 - Graceful degradation for malformed headers
            # Malformed Content-Disposition header: log and skip filename extraction.
            logger.debug(
                "Failed to parse Content-Disposition header for filename extraction",
            )
            return None
        return None

    def _on_part_begin(self) -> None:
        """Called when a new part begins."""
        self._current_field_name = None
        self._current_header_name = b""
        self._current_header_value = b""
        self._in_file_field = False

    def _on_header_field(self, data: bytes, start: int, end: int) -> None:
        """Called for header field data (header name)."""
        self._current_header_name += data[start:end]

    def _on_header_value(self, data: bytes, start: int, end: int) -> None:
        """Called for header value data."""
        self._current_header_value += data[start:end]

    def _on_header_end(self) -> None:
        """Called when a header is complete."""
        header_name = self._current_header_name.lower()
        header_value = self._current_header_value

        if header_name == b"content-disposition":
            # Extract field name
            cd_str = header_value.decode("utf-8", errors="replace")
            name_match = re.search(r'name="([^"]+)"', cd_str)
            if name_match:
                self._current_field_name = name_match.group(1)
                # Check if this is the file field
                if self._current_field_name == "file":
                    self._in_file_field = True
                    self._file_part_found = True
                    self._filename = self._extract_filename(header_value)
                    # Create temp file on data volume (not /tmp)
                    self._temp_file_fd, temp_path = tempfile.mkstemp(
                        dir=self._temp_path, prefix="upload_", suffix=".tmp"
                    )
                    self._temp_file_path = Path(temp_path)

        # Reset for next header
        self._current_header_name = b""
        self._current_header_value = b""

    def _on_part_data(self, data: bytes, start: int, end: int) -> None:
        """Called for part data (file content or form field value)."""
        if self._in_file_field and self._temp_file_fd is not None:
            chunk = data[start:end]
            # Write directly to temp file
            os.write(self._temp_file_fd, chunk)
            # Update hasher incrementally
            self._hasher.update(chunk)
            self._total_bytes += len(chunk)
            # Early size check
            if self._max_size is not None and self._total_bytes > self._max_size:
                raise UploadSizeExceededError(
                    f"Upload exceeds maximum size of {self._max_size} bytes"
                )

    def get_callbacks(self) -> dict:
        """Get callback dictionary for MultipartParser."""
        return {
            "on_part_begin": self._on_part_begin,
            "on_header_field": self._on_header_field,
            "on_header_value": self._on_header_value,
            "on_header_end": self._on_header_end,
            "on_part_data": self._on_part_data,
        }

    def finalize(self) -> None:
        """Close temp file descriptor after parsing is complete."""
        if self._temp_file_fd is not None:
            os.close(self._temp_file_fd)
            self._temp_file_fd = None

    def cleanup(self) -> None:
        """Clean up temp file on error."""
        if self._temp_file_fd is not None:
            try:
                os.close(self._temp_file_fd)
            except OSError:
                pass
            self._temp_file_fd = None
        if self._temp_file_path is not None and self._temp_file_path.exists():
            self._temp_file_path.unlink(missing_ok=True)

    def get_result(self) -> tuple[Path, str, int] | None:
        """Get the uploaded file result.

        Returns:
            Tuple of (temp_file_path, hash, bytes_written) if file was uploaded,
            None if no file part was found.
        """
        if not self._file_part_found:
            return None
        if self._temp_file_path is None:
            return None
        return (self._temp_file_path, self._hasher.hexdigest(), self._total_bytes)

    @property
    def filename(self) -> str | None:
        """Get the uploaded filename."""
        return self._filename


class UploadResponse(BaseModel):
    """Response model for successful artifact upload."""

    hash: str  # Full SHA-256 hash
    hash_ref: str  # Short hash ref like @abc12345
    artifact_path: str  # Path where artifact was stored
    is_duplicate: bool  # True if blob already existed
    download_url: str  # URL to download artifact


@router.post("/api/v1/upload/{path:path}")
async def upload_artifact(
    path: str,
    request: Request,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings: Annotated[MagpieSettings, Depends(get_magpie_settings)],
    _write_scope_check: Annotated[None, Depends(require_write_scope)] = None,
    source_uri: Annotated[str | None, Query(max_length=2048)] = None,
    no_latest: Annotated[bool, Query()] = False,
    uploaded_by: str = "anonymous",
    x_magpie_user: Annotated[str | None, Header(alias="X-Magpie-User")] = None,
    content_type: Annotated[str | None, Header(alias="Content-Type")] = None,
    content_length: Annotated[int | None, Header(alias="Content-Length")] = None,
) -> UploadResponse:
    """Upload an artifact to the storage system.

    Requires write or admin scope. Read-only tokens will be rejected with 403.

    Stores the uploaded file at the specified artifact path. If an artifact
    with identical content already exists at this path, returns the existing
    artifact info with is_duplicate=True.

    The uploader identity is determined by:
    1. X-Magpie-User header (set by Caddy forward_auth on authenticated requests)
    2. uploaded_by query parameter (fallback for direct API access)

    Args:
        path: Logical path for the artifact (e.g., "project/component").
        request: FastAPI Request object for streaming body access.
        settings: Application settings for size limit configuration.
        source_uri: Optional source URI for provenance tracking.
        no_latest: If True, skip creating/updating the "latest" tag (default: False).
        uploaded_by: Identity of the uploader (fallback, default: "anonymous").
        x_magpie_user: Authenticated user from Caddy forward_auth header.
        content_type: Content-Type header containing multipart boundary.
        content_length: Content-Length header for early size validation.

    Returns:
        UploadResponse with artifact details including hash, download URL,
        and duplicate detection status.

    Raises:
        HTTPException 400: If Content-Type is missing or boundary cannot be extracted.
        HTTPException 401: If X-Magpie-Scope header is missing (unauthenticated).
        HTTPException 403: If token has read scope (insufficient permissions).
        HTTPException 413: If upload exceeds max_upload_size configuration.
        StorageError: If storage operation fails.
    """
    # Defense-in-depth size limiting strategy:
    # 1. Content-Length check (below): Fast early rejection before reading body.
    #    Catches well-behaved clients with oversized uploads immediately.
    #    NOTE: Content-Length includes multipart overhead (~200 bytes), so this check
    #    is stricter than the file content limit. Some valid uploads near the limit
    #    may be rejected early. This is intentional - we prefer DoS protection over
    #    allowing the last ~200 bytes of capacity.
    # 2. StreamingMultipartHandler: Enforces limit during parsing on file content only.
    #    Catches malicious clients that lie about Content-Length or omit it.
    max_size = settings.max_upload_size
    if max_size is not None and content_length is not None:
        if content_length > max_size:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Upload size {content_length} exceeds maximum allowed size of {max_size} bytes",
            )

    # Normalize path for defense in depth (CLI should also normalize)
    try:
        path = normalize_artifact_path(path)
    except InvalidArtifactPathError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Extract multipart boundary from Content-Type header
    if not content_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Content-Type header is required for multipart upload",
        )

    # Verify Content-Type is multipart/form-data
    if not content_type.lower().startswith("multipart/form-data"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Content-Type must be multipart/form-data",
        )

    # Parse boundary from Content-Type: multipart/form-data; boundary=----WebKitFormBoundary...
    # Handle both quoted and unquoted boundaries per RFC 2046
    boundary_match = re.search(r'boundary="([^"]+)"', content_type)
    if not boundary_match:
        # Try unquoted format
        boundary_match = re.search(r"boundary=([^;\s]+)", content_type)
    if not boundary_match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not extract boundary from Content-Type header",
        )
    boundary = boundary_match.group(1).encode("utf-8")

    # Use X-Magpie-User header if present (authenticated via Caddy)
    # Fall back to query parameter for direct API access
    if x_magpie_user is not None:
        effective_user = x_magpie_user
    else:
        effective_user = uploaded_by
        if uploaded_by == "anonymous":
            logger.warning(
                "anonymous_upload",
                artifact_path=path,
                message="Upload without X-Magpie-User header and no uploaded_by param",
            )

    # Ensure temp directory exists
    temp_dir = settings.temp_path
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Set up streaming multipart handler
    handler = StreamingMultipartHandler(boundary, temp_dir, max_size)
    parser = MultipartParser(boundary, handler.get_callbacks())

    # Stream request body through multipart parser
    # This writes chunks directly to temp file while computing hash
    try:
        async for chunk in request.stream():
            parser.write(chunk)
        parser.finalize()
        handler.finalize()
    except UploadSizeExceededError:
        handler.cleanup()
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Upload exceeds maximum size of {max_size} bytes",
        )
    except Exception:
        handler.cleanup()
        raise

    # Get the parsed file result
    result = handler.get_result()
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing 'file' part in multipart upload",
        )

    temp_file_path, full_hash, bytes_written = result

    # Track upload timing
    start_time = time.perf_counter()

    try:
        info, is_duplicate = storage_service.store_artifact_from_temp(
            artifact_path=path,
            temp_file_path=temp_file_path,
            full_hash=full_hash,
            uploaded_by=effective_user,
            source_uri=source_uri,
            no_latest=no_latest,
        )
    except Exception:
        # Clean up temp file on error
        temp_file_path.unlink(missing_ok=True)
        raise

    # Calculate upload duration
    duration_ms = (time.perf_counter() - start_time) * 1000

    # Use blobs/ path for hash-based downloads (info.hash_ref has @ prefix)
    blob_name = info.hash_ref.lstrip("@")
    download_url = f"/artifacts/{path}/blobs/{blob_name}"

    # Log upload completion with structured fields
    logger.info(
        "upload_complete",
        artifact_path=path,
        hash=info.hash,
        hash_ref=info.hash_ref,
        size_bytes=bytes_written,  # Actual file size, not including multipart overhead
        duration_ms=round(duration_ms, 2),
        uploaded_by=effective_user,
        is_duplicate=is_duplicate,
        source_uri=source_uri,
    )

    return UploadResponse(
        hash=info.hash,
        hash_ref=info.hash_ref,
        artifact_path=path,
        is_duplicate=is_duplicate,
        download_url=download_url,
    )
