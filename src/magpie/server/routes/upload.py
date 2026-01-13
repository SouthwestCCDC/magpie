"""Upload endpoint for storing artifacts."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, UploadFile, status
from pydantic import BaseModel

from magpie.server.deps import get_storage_service, require_write_scope
from magpie.storage.exceptions import InvalidArtifactPathError
from magpie.storage.paths import normalize_artifact_path
from magpie.storage.service import StorageService

logger = logging.getLogger(__name__)

router = APIRouter()


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
    _write_scope_check: Annotated[None, Depends(require_write_scope)] = None,
    source_uri: Annotated[str | None, Query(max_length=2048)] = None,
    uploaded_by: str = "anonymous",
    x_magpie_user: Annotated[str | None, Header(alias="X-Magpie-User")] = None,
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
        source_uri: Optional source URI for provenance tracking.
        uploaded_by: Identity of the uploader (fallback, default: "anonymous").
        x_magpie_user: Authenticated user from Caddy forward_auth header.

    Returns:
        UploadResponse with artifact details including hash, download URL,
        and duplicate detection status.

    Raises:
        HTTPException 401: If X-Magpie-Scope header is missing (unauthenticated).
        HTTPException 403: If token has read scope (insufficient permissions).
        StorageError: If storage operation fails.
    """
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

    info, is_duplicate = storage_service.store_artifact(
        artifact_path=path,
        file_stream=file.file,
        uploaded_by=effective_user,
        source_uri=source_uri,
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
