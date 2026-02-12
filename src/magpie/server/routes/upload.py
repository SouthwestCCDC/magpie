"""Upload endpoint for storing artifacts."""

from __future__ import annotations

import asyncio
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


class HeaderLimitExceededError(Exception):
    """Raised when multipart headers exceed safety limits."""


class MalformedMultipartError(Exception):
    """Raised when multipart payload is malformed or missing required headers."""


class StreamingMultipartHandler:
    """Handler for streaming multipart/form-data without buffering.

    This class uses python-multipart's streaming parser to extract file content
    directly from the request stream, writing chunks directly to a temp file on
    the data volume while computing the SHA-256 hash incrementally. This achieves
    true single-pass streaming: network → parse → hash → temp file.
    """

    # Safety limits for malicious multipart payloads (DoS prevention)
    MAX_HEADER_SIZE = 16 * 1024  # 16KB per header (combined name + value)
    MAX_HEADERS_PER_PART = 50  # Maximum headers per multipart part

    def __init__(self, temp_path: Path, max_size: int | None = None) -> None:
        """Initialize streaming multipart handler.

        Args:
            temp_path: Directory for temporary files (on data volume).
            max_size: Maximum allowed upload size (for early rejection).
        """
        self._temp_path = temp_path
        self._max_size = max_size
        self._temp_file = None  # Buffered file object (handles partial writes internally)
        self._temp_file_path: Path | None = None
        self._hasher = hashlib.sha256()
        self._current_field_name: str | None = None
        self._current_header_name: bytes = b""
        self._current_header_value: bytes = b""
        self._filename: str | None = None
        self._in_file_field = False
        self._total_bytes = 0  # Part body content bytes (all fields, for DoS prevention)
        self._file_bytes = 0  # File field data only (for accurate size reporting)
        self._file_part_found = False
        self._current_part_header_count = 0
        self._current_part_has_content_disposition = False

    def _extract_filename(self, content_disposition: bytes) -> str | None:
        """Extract filename from Content-Disposition header."""
        # Parse: Content-Disposition: form-data; name="file"; filename="artifact.bin"
        try:
            cd_str = content_disposition.decode("utf-8", errors="replace")
            # Look for filename="..." (quoted) or filename=... (unquoted)
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
        self._current_part_header_count = 0
        self._current_part_has_content_disposition = False

    def _on_header_field(self, data: bytes, start: int, end: int) -> None:
        """Called for header field data (header name)."""
        self._current_header_name += data[start:end]
        # Enforce combined header size limit (DoS prevention)
        if len(self._current_header_name) + len(self._current_header_value) > self.MAX_HEADER_SIZE:
            raise HeaderLimitExceededError(
                f"Multipart header (name + value) exceeds maximum size of {self.MAX_HEADER_SIZE} bytes"
            )

    def _on_header_value(self, data: bytes, start: int, end: int) -> None:
        """Called for header value data."""
        self._current_header_value += data[start:end]
        # Enforce combined header size limit (DoS prevention)
        if len(self._current_header_name) + len(self._current_header_value) > self.MAX_HEADER_SIZE:
            raise HeaderLimitExceededError(
                f"Multipart header (name + value) exceeds maximum size of {self.MAX_HEADER_SIZE} bytes"
            )

    def _on_header_end(self) -> None:
        """Called when a header is complete."""
        # Enforce header count limit per part (DoS prevention)
        self._current_part_header_count += 1
        if self._current_part_header_count > self.MAX_HEADERS_PER_PART:
            raise HeaderLimitExceededError(
                f"Multipart part has more than {self.MAX_HEADERS_PER_PART} headers"
            )

        header_name = self._current_header_name.lower()
        header_value = self._current_header_value

        if header_name == b"content-disposition":
            self._current_part_has_content_disposition = True
            # Extract field name (RFC 7578 allows both quoted and unquoted values)
            cd_str = header_value.decode("utf-8", errors="replace")
            # Match: name="value" (quoted) or name=value (unquoted)
            name_match = re.search(r'name=(?:"([^"]+)"|([^;\s]+))', cd_str)
            if not name_match:
                # Content-Disposition present but no field name - malformed
                raise MalformedMultipartError(
                    "Content-Disposition header missing required 'name' parameter"
                )
            # Group 1 for quoted, group 2 for unquoted
            self._current_field_name = (name_match.group(1) or name_match.group(2)).strip()
            # Check if this is the file field
            if self._current_field_name == "file":
                # Reject multiple file parts to prevent resource leaks and ambiguity
                if self._file_part_found:
                    raise MalformedMultipartError(
                        "Multiple 'file' parts not allowed in multipart upload"
                    )
                self._in_file_field = True
                self._file_part_found = True
                self._filename = self._extract_filename(header_value)
                # Create temp file on data volume (not /tmp)
                # Use os.fdopen to get a buffered file object that handles partial writes
                temp_fd, temp_path = tempfile.mkstemp(
                    dir=self._temp_path, prefix="upload_", suffix=".tmp"
                )
                self._temp_file_path = Path(temp_path)
                # fdopen takes ownership of the fd, so we don't need to track it separately
                self._temp_file = os.fdopen(temp_fd, "wb")

        # Reset for next header
        self._current_header_name = b""
        self._current_header_value = b""

    def _on_part_data(self, data: bytes, start: int, end: int) -> None:
        """Called for part data (file content or form field value)."""
        # Validate that Content-Disposition header was present before processing data
        if not self._current_part_has_content_disposition:
            raise MalformedMultipartError(
                "Multipart part missing required Content-Disposition header"
            )

        # Track total bytes for ALL part body content (DoS prevention against payload bloat)
        chunk = data[start:end]
        self._total_bytes += len(chunk)
        # Enforce size limit on part body content bytes (file field + other form fields)
        # Note: Does NOT count multipart headers or boundaries (parser strips these before callbacks)
        # This prevents DoS via bloated non-file fields.
        if self._max_size is not None and self._total_bytes > self._max_size:
            raise UploadSizeExceededError(f"Upload exceeds maximum size of {self._max_size} bytes")

        # File-specific operations (write, hash, count) only for file field
        if self._in_file_field and self._temp_file is not None:
            # Write to buffered file object (handles partial writes internally)
            self._temp_file.write(chunk)
            # Update hasher incrementally
            self._hasher.update(chunk)
            # Track file bytes separately for accurate size reporting
            self._file_bytes += len(chunk)

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
        """Close temp file after parsing is complete."""
        if self._temp_file is not None:
            self._temp_file.close()
            self._temp_file = None

    def cleanup(self) -> None:
        """Clean up temp file on error."""
        if self._temp_file is not None:
            try:
                self._temp_file.close()
            except OSError as exc:
                # Best-effort cleanup: log and continue even if closing fails.
                logger.warning(
                    "Failed to close temporary upload file during cleanup",
                    exc_info=exc,
                )
            self._temp_file = None
        if self._temp_file_path is not None and self._temp_file_path.exists():
            self._temp_file_path.unlink(missing_ok=True)

    def get_result(self) -> tuple[Path, str, int] | None:
        """Get the uploaded file result.

        Returns:
            Tuple of (temp_file_path, hash, file_bytes) if file was uploaded,
            None if no file part was found.
            Note: file_bytes is the size of the file field data only, excluding
            multipart overhead and other form fields.
        """
        if not self._file_part_found:
            return None
        if self._temp_file_path is None:
            return None
        return (self._temp_file_path, self._hasher.hexdigest(), self._file_bytes)

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
    # 2. StreamingMultipartHandler: Enforces limit during parsing on total multipart payload.
    #    Tracks ALL bytes (file data, form fields, headers, boundaries) to prevent DoS.
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
    # RFC 2046 allows boundaries up to 70 characters, consisting of alphanumeric and certain special chars
    # Quoted boundaries can contain spaces and special characters, unquoted cannot
    boundary_match = re.search(
        r'boundary\s*=\s*"([^"]+)"', content_type, flags=re.IGNORECASE
    )  # Quoted format
    if not boundary_match:
        # Try unquoted format: RFC 2046 says unquoted boundaries use token syntax
        # (alphanumeric plus certain special chars, no spaces)
        boundary_match = re.search(
            r"boundary\s*=\s*([!#$%&'*+.0-9A-Z^_`a-z|~-]+)", content_type, flags=re.IGNORECASE
        )
    if not boundary_match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not extract boundary from Content-Type header",
        )
    boundary_str = boundary_match.group(1)
    # RFC 2046: boundary must be 1-70 characters
    if not boundary_str or len(boundary_str) > 70:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Boundary must be 1-70 characters per RFC 2046",
        )
    boundary = boundary_str.encode("utf-8")

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
    handler = StreamingMultipartHandler(temp_dir, max_size)
    parser = MultipartParser(boundary, handler.get_callbacks())

    # Stream request body through multipart parser in a single background thread
    # Bridge async stream → sync parser using a queue (avoids per-chunk thread overhead)
    chunk_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=10)
    loop = asyncio.get_running_loop()

    async def stream_producer():
        """Read chunks from request stream and put them into queue."""
        try:
            async for chunk in request.stream():
                await chunk_queue.put(chunk)
        finally:
            # Signal end of stream with sentinel value
            # Must be blocking to ensure parser receives it on normal completion.
            # If parser exits early (error), producer_task is cancelled by outer finally block.
            await chunk_queue.put(None)

    def parser_worker():
        """Background thread: consume chunks from queue and write to disk."""
        try:
            while True:
                # Blocking get from async queue (loop captured from async context)
                chunk = asyncio.run_coroutine_threadsafe(chunk_queue.get(), loop).result()
                if chunk is None:
                    # End-of-stream sentinel
                    break
                parser.write(chunk)
            parser.finalize()
            handler.finalize()
        except Exception:
            # Re-raise for asyncio.to_thread to propagate to main task
            raise

    producer_task = None
    temp_file_path = None
    try:
        # Start producer task and worker thread concurrently
        producer_task = asyncio.create_task(stream_producer())
        # Run entire parsing loop in single background thread
        await asyncio.to_thread(parser_worker)
        # Wait for producer to finish (should already be done)
        await producer_task
    except UploadSizeExceededError:
        handler.cleanup()
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Upload exceeds maximum size of {max_size} bytes",
        )
    except HeaderLimitExceededError as e:
        handler.cleanup()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except MalformedMultipartError as e:
        handler.cleanup()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception:
        handler.cleanup()
        raise
    finally:
        # Ensure producer task is cancelled if still running (DoS prevention)
        # This handles cases where parser_worker() raises before consuming all chunks
        if producer_task is not None and not producer_task.done():
            producer_task.cancel()
            try:
                await producer_task
            except asyncio.CancelledError:
                # Expected when cancelling - suppress
                pass

    # Get the parsed file result
    result = handler.get_result()
    if result is None:
        # Defensive cleanup for missing file part (temp file may still exist)
        try:
            handler.cleanup()
        except Exception:  # nosec B110 - Best-effort cleanup, failure is non-critical
            # Cleanup failure is non-critical since we're already raising an error
            # for missing file part. Log it but don't let it mask the real error.
            pass
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
    except InvalidArtifactPathError:
        # Clean up temp file on invalid artifact path
        # Let exception propagate to global handler for proper error formatting
        temp_file_path.unlink(missing_ok=True)
        raise
    except Exception:
        # Clean up temp file on error
        # Note: handler.cleanup() was already called by finalize() on success,
        # so we only need to clean up the temp file path here
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
