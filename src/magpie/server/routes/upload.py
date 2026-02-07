"""Upload endpoint for storing artifacts."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Annotated

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
            # Extract field name (RFC 7578 allows both quoted and unquoted values)
            cd_str = header_value.decode("utf-8", errors="replace")
            # Match: name="value" (quoted) or name=value (unquoted)
            name_match = re.search(r'name=(?:"([^"]+)"|([^;\s]+))', cd_str)
            if name_match:
                # Group 1 for quoted, group 2 for unquoted
                self._current_field_name = (name_match.group(1) or name_match.group(2)).strip()
                # Check if this is the file field
                if self._current_field_name == "file":
                    # Reject multiple file parts to prevent resource leaks and ambiguity
                    if self._file_part_found:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Multiple 'file' parts not allowed in multipart upload",
                        )
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
            # Write directly to temp file, handling partial writes
            # os.write() may return less than len(chunk) on full disk or signal interrupts
            bytes_written = os.write(self._temp_file_fd, chunk)
            if bytes_written != len(chunk):
                raise IOError(
                    f"Partial write to temp file: expected {len(chunk)} bytes, wrote {bytes_written}"
                )
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
            except OSError as exc:
                # Best-effort cleanup: log and continue even if closing fails.
                logger.warning(
                    "Failed to close temporary upload file descriptor during cleanup",
                    exc_info=exc,
                )
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
    # Parameter names are case-insensitive per RFC 2046
    boundary_match = re.search(r'boundary\s*=\s*"([^"]+)"', content_type, flags=re.IGNORECASE)
    if not boundary_match:
        # Try unquoted format
        boundary_match = re.search(r"boundary\s*=\s*([^;\s]+)", content_type, flags=re.IGNORECASE)
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
