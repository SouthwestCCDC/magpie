"""Status endpoint for server health, version, and storage statistics."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from magpie import __version__
from magpie.auth.models import TokenScope
from magpie.auth.service import TokenService
from magpie.server.deps import get_storage_service, get_token_service
from magpie.storage.service import StorageService

router = APIRouter()


class AuthStatus(BaseModel):
    """Token authentication status."""

    valid: bool
    scope: str | None = None
    name: str | None = None


class StorageStats(BaseModel):
    """Storage usage statistics."""

    total_size_bytes: int
    artifact_count: int
    blob_count: int


class StatusResponse(BaseModel):
    """Response model for server status endpoint."""

    status: str
    version: str
    auth: AuthStatus
    storage: StorageStats


def _get_auth_status(
    authorization: str | None,
    token_service: TokenService,
) -> AuthStatus:
    """Validate token and return auth status.

    Args:
        authorization: Authorization header value (Bearer <token>).
        token_service: TokenService instance.

    Returns:
        AuthStatus with validation result.
    """
    if authorization is None:
        return AuthStatus(valid=False)

    if not authorization.startswith("Bearer "):
        return AuthStatus(valid=False)

    token = authorization[7:]  # Remove "Bearer " prefix
    token_info = token_service.validate_token(token)

    if token_info is None:
        return AuthStatus(valid=False)

    return AuthStatus(
        valid=True,
        scope=token_info.scope.value,
        name=token_info.name,
    )


def _get_storage_stats(storage_service: StorageService) -> StorageStats:
    """Calculate storage statistics.

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
    token_service: Annotated[TokenService, Depends(get_token_service)],
    authorization: Annotated[str | None, Header()] = None,
) -> StatusResponse:
    """Get server status including health, version, auth status, and storage stats.

    This endpoint provides a comprehensive view of the server state:
    - Server health status (always "ok" if responding)
    - Server version
    - Token authentication status (if Authorization header provided)
    - Storage usage statistics

    The token validation is optional - if no Authorization header is provided,
    auth status will show valid=False.

    Args:
        storage_service: StorageService instance.
        token_service: TokenService instance.
        authorization: Optional Authorization header for token validation.

    Returns:
        StatusResponse with server status information.
    """
    auth_status = _get_auth_status(authorization, token_service)
    storage_stats = _get_storage_stats(storage_service)

    return StatusResponse(
        status="ok",
        version=__version__,
        auth=auth_status,
        storage=storage_stats,
    )
