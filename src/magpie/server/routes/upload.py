"""Upload endpoint for storing artifacts."""

from __future__ import annotations

import logging
from typing import IO, Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, UploadFile, status
from pydantic import BaseModel

from magpie.config import MagpieSettings
from magpie.server.deps import get_magpie_settings, get_storage_service, require_write_scope
from magpie.storage.exceptions import InvalidArtifactPathError
from magpie.storage.paths import normalize_artifact_path
from magpie.storage.service import StorageService

logger = logging.getLogger(__name__)

router = APIRouter()


class UploadSizeExceededError(Exception):
    """Raised when upload exceeds configured max_upload_size."""


class SizeLimitedReader:
    """Wrapper around a file stream that enforces a maximum size limit.

    This wrapper tracks bytes read from the underlying stream and raises
    UploadSizeExceededError if the limit is exceeded during streaming.
    """

    def __init__(self, stream: IO[bytes], max_size: int) -> None:
        """Initialize size-limited reader.

        Args:
            stream: Underlying file stream to wrap.
            max_size: Maximum allowed bytes to read.
        """
        self._stream = stream
        self._max_size = max_size
        self._bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        """Read from stream, enforcing size limit.

        Args:
            size: Number of bytes to read (-1 for all).

        Returns:
            Bytes read from stream.

        Raises:
            UploadSizeExceededError: If cumulative read exceeds max_size.
        """
        data = self._stream.read(size)
        self._bytes_read += len(data)
        if self._bytes_read > self._max_size:
            # NOTE: Revealing the exact limit is intentional - it helps legitimate users
            # understand the constraint. The limit is not security-sensitive information;
            # it's a configuration value that would be documented anyway.
            raise UploadSizeExceededError(f"Upload exceeds maximum size of {self._max_size} bytes")
        return data

    def seek(self, pos: int, whence: int = 0) -> int:
        """Seek in stream and reset bytes_read counter appropriately.

        SECURITY: Must reset _bytes_read to prevent bypass via seek-then-read.
        For SEEK_SET (whence=0), we know the exact position.
        For SEEK_END (whence=2), we cannot accurately track position, so reset
        to 0 to be conservative (may over-count on subsequent reads).
        """
        result = self._stream.seek(pos, whence)
        if whence == 0:  # SEEK_SET
            self._bytes_read = pos
        elif whence == 2:  # SEEK_END - can't track accurately
            self._bytes_read = 0  # Reset to be safe
        return result

    def tell(self) -> int:
        """Get current position in stream (pass-through)."""
        return self._stream.tell()


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
    file: UploadFile,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings: Annotated[MagpieSettings, Depends(get_magpie_settings)],
    _write_scope_check: Annotated[None, Depends(require_write_scope)] = None,
    source_uri: Annotated[str | None, Query(max_length=2048)] = None,
    uploaded_by: str = "anonymous",
    x_magpie_user: Annotated[str | None, Header(alias="X-Magpie-User")] = None,
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
        file: File content to upload (streamed).
        settings: Application settings for size limit configuration.
        source_uri: Optional source URI for provenance tracking.
        uploaded_by: Identity of the uploader (fallback, default: "anonymous").
        x_magpie_user: Authenticated user from Caddy forward_auth header.
        content_length: Content-Length header for early size validation.

    Returns:
        UploadResponse with artifact details including hash, download URL,
        and duplicate detection status.

    Raises:
        HTTPException 401: If X-Magpie-Scope header is missing (unauthenticated).
        HTTPException 403: If token has read scope (insufficient permissions).
        HTTPException 413: If upload exceeds max_upload_size configuration.
        StorageError: If storage operation fails.
    """
    # Defense-in-depth size limiting strategy:
    # 1. Content-Length check (below): Fast early rejection before reading body.
    #    Catches well-behaved clients with oversized uploads immediately.
    # 2. SizeLimitedReader (later): Enforces limit during streaming.
    #    Catches malicious clients that lie about Content-Length or omit it.
    # Both checks use the same max_upload_size limit for file content.
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

    # Use X-Magpie-User header if present (authenticated via Caddy)
    # Fall back to query parameter for direct API access
    if x_magpie_user is not None:
        effective_user = x_magpie_user
    else:
        effective_user = uploaded_by
        if uploaded_by == "anonymous":
            logger.warning(
                "Upload request without X-Magpie-User header and no uploaded_by param, "
                "using 'anonymous' - this may indicate unauthenticated access"
            )

    # Wrap file stream with size limiter if max_upload_size is configured
    # This provides defense-in-depth for clients that send more than Content-Length
    if max_size is not None:
        file_stream = SizeLimitedReader(file.file, max_size)
    else:
        file_stream = file.file

    try:
        info, is_duplicate = storage_service.store_artifact(
            artifact_path=path,
            file_stream=file_stream,
            uploaded_by=effective_user,
            source_uri=source_uri,
        )
    except UploadSizeExceededError as e:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(e),
        )

    # Use blobs/ path for hash-based downloads (info.hash_ref has @ prefix)
    blob_name = info.hash_ref.lstrip("@")
    download_url = f"/artifacts/{path}/blobs/{blob_name}"

    return UploadResponse(
        hash=info.hash,
        hash_ref=info.hash_ref,
        artifact_path=path,
        is_duplicate=is_duplicate,
        download_url=download_url,
    )
