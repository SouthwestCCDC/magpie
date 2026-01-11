"""Artifacts endpoints for listing, info, and tag management."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from magpie.server.deps import get_storage_service, require_write_scope
from magpie.storage.exceptions import ArtifactNotFoundError
from magpie.storage.service import StorageService

router = APIRouter()


class VersionInfo(BaseModel):
    """Information about a single artifact version."""

    hash: str  # Full SHA-256 hash
    hash_ref: str  # Short hash ref like @abc12345
    tags: list[str]  # Tags pointing to this version
    uploaded_by: str
    uploaded_at: datetime
    source_uri: str | None


class ArtifactListResponse(BaseModel):
    """Response model for artifact listing."""

    artifact_path: str
    versions: list[VersionInfo]


class ArtifactInfoResponse(BaseModel):
    """Response model for specific artifact version info."""

    hash: str  # Full SHA-256 hash
    hash_ref: str  # Short hash ref like @abc12345
    uploaded_by: str
    uploaded_at: datetime
    source_uri: str | None
    tags: list[str]  # All tags pointing to this version


class CreateTagRequest(BaseModel):
    """Request model for creating a tag."""

    tag_name: str  # Name for the new tag


class TagResponse(BaseModel):
    """Response model for tag operations."""

    artifact_path: str
    tag_name: str
    hash_ref: str  # Hash the tag now points to
    tags: list[str]  # Updated list of all tags on this version


class AmendMetadataRequest(BaseModel):
    """Request model for amending artifact metadata."""

    source_uri: str | None = None  # New source URI (None to leave unchanged)


# NOTE: Info endpoint must be registered BEFORE list endpoint
# because {path:path} is greedy and would capture the /info suffix
@router.get("/api/v1/artifacts/{path:path}/{ref}/info")
async def get_artifact_info(
    path: str,
    ref: str,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
) -> ArtifactInfoResponse:
    """Get metadata for a specific artifact version.

    Resolves the ref parameter as either a tag name (e.g., "latest") or a
    hash reference (e.g., "@abc12345") and returns the artifact's metadata.

    Args:
        path: Logical path for the artifact (e.g., "project/component").
        ref: Tag name or hash reference to resolve.

    Returns:
        ArtifactInfoResponse with full metadata for the resolved version.

    Raises:
        ArtifactNotFoundError: If path doesn't exist or ref doesn't resolve.
            Automatically converted to HTTP 404 by error handlers.
    """
    info = storage_service.get_artifact_info(path, ref)

    return ArtifactInfoResponse(
        hash=info.hash,
        hash_ref=info.hash_ref,
        uploaded_by=info.uploaded_by,
        uploaded_at=info.uploaded_at,
        source_uri=info.source_uri,
        tags=info.tags,
    )


# NOTE: Tag endpoints must be registered BEFORE list endpoint
# because {path:path} is greedy and would capture the /tags suffix
@router.post("/api/v1/artifacts/{path:path}/{ref}/tags")
async def create_tag(
    path: str,
    ref: str,
    request: CreateTagRequest,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    _write_scope_check: Annotated[None, Depends(require_write_scope)] = None,
) -> TagResponse:
    """Create or update a tag pointing to a specific artifact version.

    Requires write or admin scope. Read-only tokens will be rejected with 403.

    Creates a named tag that points to the blob identified by ref. If the tag
    already exists, it will be updated to point to the new blob.

    Args:
        path: Logical path for the artifact (e.g., "project/component").
        ref: Tag name or hash reference identifying the blob to tag.
        request: Request body containing the tag_name to create.

    Returns:
        TagResponse with the created tag info and updated tags list.

    Raises:
        HTTPException 401: If X-Magpie-Scope header is missing (unauthenticated).
        HTTPException 403: If token has read scope (insufficient permissions).
        ArtifactNotFoundError: If path doesn't exist or ref doesn't resolve.
            Automatically converted to HTTP 404 by error handlers.
    """
    # Resolve ref (tag name or hash ref) to get the hash_ref
    # This handles both "latest" (tag) and "@abc123" (hash ref) formats
    existing_info = storage_service.get_artifact_info(path, ref)

    # Now create the tag using the resolved hash_ref
    info = storage_service.create_tag(path, existing_info.hash_ref, request.tag_name)

    return TagResponse(
        artifact_path=path,
        tag_name=request.tag_name,
        hash_ref=info.hash_ref,
        tags=info.tags,
    )


@router.delete(
    "/api/v1/artifacts/{path:path}/tags/{tag_name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_tag(
    path: str,
    tag_name: str,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    _write_scope_check: Annotated[None, Depends(require_write_scope)] = None,
) -> Response:
    """Remove a tag from an artifact.

    Requires write or admin scope. Read-only tokens will be rejected with 403.

    Deletes the named tag from the artifact. The underlying blob is not affected.

    Args:
        path: Logical path for the artifact (e.g., "project/component").
        tag_name: Name of the tag to remove.

    Returns:
        204 No Content on success.

    Raises:
        HTTPException 401: If X-Magpie-Scope header is missing (unauthenticated).
        HTTPException 403: If token has read scope (insufficient permissions).
        ArtifactNotFoundError: If path doesn't exist or tag doesn't exist.
            Automatically converted to HTTP 404 by error handlers.
    """
    removed = storage_service.remove_tag(path, tag_name)

    if not removed:
        raise ArtifactNotFoundError(f"Tag '{tag_name}' not found in artifact {path}")

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# NOTE: Amend endpoint must be registered BEFORE list endpoint
# because {path:path} is greedy and would capture additional path segments
@router.patch("/api/v1/artifacts/{path:path}/{ref}")
async def amend_metadata(
    path: str,
    ref: str,
    request: AmendMetadataRequest,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    _write_scope_check: Annotated[None, Depends(require_write_scope)] = None,
) -> ArtifactInfoResponse:
    """Amend metadata for a specific artifact version.

    Requires write or admin scope. Read-only tokens will be rejected with 403.

    Updates mutable metadata fields on an existing blob. Immutable fields
    (hash, uploaded_by, uploaded_at) are preserved.

    Args:
        path: Logical path for the artifact (e.g., "project/component").
        ref: Tag name or hash reference identifying the blob to amend.
        request: Request body containing fields to update.

    Returns:
        ArtifactInfoResponse with the updated metadata.

    Raises:
        HTTPException 401: If X-Magpie-Scope header is missing (unauthenticated).
        HTTPException 403: If token has read scope (insufficient permissions).
        ArtifactNotFoundError: If path doesn't exist or ref doesn't resolve.
            Automatically converted to HTTP 404 by error handlers.
    """
    # Resolve ref (tag name or hash ref) to get the hash_ref
    existing_info = storage_service.get_artifact_info(path, ref)

    # Amend metadata using the resolved hash_ref
    info = storage_service.amend_metadata(
        artifact_path=path,
        hash_ref=existing_info.hash_ref,
        source_uri=request.source_uri,
    )

    return ArtifactInfoResponse(
        hash=info.hash,
        hash_ref=info.hash_ref,
        uploaded_by=info.uploaded_by,
        uploaded_at=info.uploaded_at,
        source_uri=info.source_uri,
        tags=info.tags,
    )


@router.get("/api/v1/artifacts/{path:path}")
async def list_artifacts(
    path: str,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
) -> ArtifactListResponse:
    """List all versions of an artifact at the specified path.

    Returns all stored versions with their tags and metadata. If the artifact
    path doesn't exist or has no uploads, returns an empty versions list.

    Args:
        path: Logical path for the artifact (e.g., "project/component").

    Returns:
        ArtifactListResponse with artifact path and list of versions.
        Empty versions list if path doesn't exist.
    """
    artifact_infos = storage_service.list_artifacts(path)

    versions = [
        VersionInfo(
            hash=info.hash,
            hash_ref=info.hash_ref,
            tags=info.tags,
            uploaded_by=info.uploaded_by,
            uploaded_at=info.uploaded_at,
            source_uri=info.source_uri,
        )
        for info in artifact_infos
    ]

    return ArtifactListResponse(
        artifact_path=path,
        versions=versions,
    )
