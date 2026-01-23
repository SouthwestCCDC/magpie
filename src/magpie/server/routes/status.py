"""Status endpoint for server health, version, and storage statistics."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from magpie import __version__
from magpie.auth.service import TokenInfo
from magpie.server.deps import get_storage_service, require_admin_scope
from magpie.storage.service import StorageService

router = APIRouter()


class StorageStats(BaseModel):
    """Storage usage statistics."""

    total_size_bytes: int
    artifact_count: int
    blob_count: int


class StatusResponse(BaseModel):
    """Response model for server status endpoint."""

    status: str
    version: str
    storage: StorageStats


def _get_storage_stats(storage_service: StorageService) -> StorageStats:
    """Calculate storage statistics.

    Note: This function walks the entire storage tree using rglob, which can
    be slow for large deployments with many artifacts. For production systems
    with thousands of artifacts, consider caching these statistics or calling
    this endpoint sparingly. See also check_artifact_nesting() for similar
    performance considerations.

    Args:
        storage_service: StorageService instance.

    Returns:
        StorageStats with current usage.
    """
    storage_path = storage_service.config.storage_path
    total_size = 0
    artifact_count = 0
    blob_count = 0

    # Count artifacts and blobs by walking storage path
    for manifest_file in storage_path.rglob(".magpie"):
        artifact_count += 1
        artifact_dir = manifest_file.parent
        blobs_dir = artifact_dir / "blobs"

        if blobs_dir.exists():
            for blob_file in blobs_dir.iterdir():
                if blob_file.is_file():
                    blob_count += 1
                    total_size += blob_file.stat().st_size

    return StorageStats(
        total_size_bytes=total_size,
        artifact_count=artifact_count,
        blob_count=blob_count,
    )


@router.get("/api/v1/status")
async def get_status(
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    admin: Annotated[TokenInfo, Depends(require_admin_scope)] = None,
) -> StatusResponse:
    """Get server status including health, version, and storage stats (admin only).

    This endpoint provides a comprehensive view of the server state:
    - Server health status (always "ok" if responding)
    - Server version
    - Storage usage statistics

    Requires admin authentication via Bearer token in Authorization header.

    Args:
        storage_service: StorageService instance.
        admin: Validated admin token (injected by dependency).

    Returns:
        StatusResponse with server status information.

    Raises:
        HTTPException 401: If token is missing, malformed, invalid, or disabled.
        HTTPException 403: If token doesn't have admin scope.
    """
    storage_stats = _get_storage_stats(storage_service)

    return StatusResponse(
        status="ok",
        version=__version__,
        storage=storage_stats,
    )
