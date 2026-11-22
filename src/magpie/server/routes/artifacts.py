"""Artifacts endpoints for listing, info, and tag management."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator

from magpie.server.deps import get_storage_service, require_read_scope, require_write_scope
from magpie.storage.exceptions import ArtifactNotFoundError, InvalidArtifactPathError
from magpie.storage.paths import normalize_artifact_path
from magpie.storage.service import StorageService
from magpie.validation import TAG_NAME_MAX_LENGTH, TAG_NAME_PATTERN

router = APIRouter()

_TAG_NAME_RE = re.compile(TAG_NAME_PATTERN)


def _normalize_path(path: str) -> str:
    """Normalize artifact path for defense in depth.

    CLI should also normalize, but server validates as well.

    Args:
        path: Raw artifact path from request.

    Returns:
        Normalized path.

    Raises:
        HTTPException 400: If path is invalid.
    """
    try:
        return normalize_artifact_path(path)
    except InvalidArtifactPathError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


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

    @field_validator("tag_name")
    @classmethod
    def validate_tag_name(cls, v: str) -> str:
        """Validate tag name matches allowed pattern.

        Tag names must start with an alphanumeric character and contain only
        alphanumeric characters, dots, underscores, and hyphens.

        Raises:
            ValueError: If tag name is invalid.
        """
        if not _TAG_NAME_RE.match(v):
            raise ValueError(
                "Tag name must start with alphanumeric and contain only "
                "alphanumeric, dots, underscores, or hyphens"
            )
        return v


class TagResponse(BaseModel):
    """Response model for tag operations."""

    artifact_path: str
    tag_name: str
    hash_ref: str  # Hash the tag now points to
    tags: list[str]  # Updated list of all tags on this version


class AmendMetadataRequest(BaseModel):
    """Request model for amending artifact metadata."""

    source_uri: str | None = Field(
        default=None, max_length=2048
    )  # New source URI (None to leave unchanged)


class ArtifactPathsResponse(BaseModel):
    """Response model for listing artifact paths."""

    paths: list[str]  # List of artifact paths matching the prefix


# NOTE: Info endpoint must be registered BEFORE list endpoint
# because {path:path} is greedy and would capture the /info suffix
@router.get("/api/v1/artifacts/{path:path}/{ref}/info")
async def get_artifact_info(
    path: str,
    ref: str,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    _read_scope_check: Annotated[None, Depends(require_read_scope)] = None,
) -> ArtifactInfoResponse:
    """Get metadata for a specific artifact version.

    Requires authentication (read, write, or admin scope). Unauthenticated
    requests will receive 401 before any path resolution occurs.

    Resolves the ref parameter as either a tag name (e.g., "latest") or a
    hash reference (e.g., "@abc12345") and returns the artifact's metadata.

    Args:
        path: Logical path for the artifact (e.g., "project/component").
        ref: Tag name or hash reference to resolve.

    Returns:
        ArtifactInfoResponse with full metadata for the resolved version.

    Raises:
        HTTPException 401: If X-Magpie-Scope header is missing (unauthenticated).
        ArtifactNotFoundError: If path doesn't exist or ref doesn't resolve.
            Automatically converted to HTTP 404 by error handlers.
    """
    path = _normalize_path(path)
    # Storage calls do blocking filesystem IO; run them in the threadpool so a
    # large artifact tree can't stall the event loop for other requests.
    info = await run_in_threadpool(storage_service.get_artifact_info, path, ref)

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
    path = _normalize_path(path)

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
    tag_name: Annotated[str, Path(pattern=TAG_NAME_PATTERN, max_length=TAG_NAME_MAX_LENGTH)],
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
    path = _normalize_path(path)
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
    path = _normalize_path(path)

    # Tag and metadata mutations deliberately stay on the event loop: unlike the
    # manifest, metadata sidecars are updated with a non-atomic read-modify-write
    # (update_metadata()), so letting them overlap would surface partial reads.
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


@router.get("/api/v1/artifacts")
async def list_artifact_paths(
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    _read_scope_check: Annotated[None, Depends(require_read_scope)] = None,
    prefix: str = "",
    recursive: bool = False,
) -> ArtifactPathsResponse:
    """List artifact paths matching a prefix.

    Requires authentication (read, write, or admin scope). Unauthenticated
    requests will receive 401 before any path resolution occurs.

    Returns artifact paths (directories with .magpie manifests). By default,
    lists only artifacts at the current level. Use recursive=true to list
    all artifacts recursively.

    Args:
        prefix: Optional path prefix to filter by (default: "" lists all).
               Leading slashes are normalized.
        recursive: If true, list all artifacts recursively. If false (default),
                  list only artifacts at the current level.

    Returns:
        ArtifactPathsResponse with list of matching artifact paths.

    Raises:
        HTTPException 401: If X-Magpie-Scope header is missing (unauthenticated).
    """
    # Normalize prefix if provided (empty string is valid for listing all)
    if prefix:
        try:
            prefix = normalize_artifact_path(prefix)
        except InvalidArtifactPathError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    paths = await run_in_threadpool(
        storage_service.list_artifact_paths, prefix, recursive=recursive
    )
    return ArtifactPathsResponse(paths=paths)


@router.get("/api/v1/artifacts/{path:path}")
async def list_artifacts(
    path: str,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    _read_scope_check: Annotated[None, Depends(require_read_scope)] = None,
) -> ArtifactListResponse:
    """List all versions of an artifact at the specified path.

    Requires authentication (read, write, or admin scope). Unauthenticated
    requests will receive 401 before any path resolution occurs.

    Returns all stored versions with their tags and metadata. If the artifact
    path doesn't exist or has no uploads, returns an empty versions list.

    Args:
        path: Logical path for the artifact (e.g., "project/component").

    Returns:
        ArtifactListResponse with artifact path and list of versions.
        Empty versions list if path doesn't exist.

    Raises:
        HTTPException 401: If X-Magpie-Scope header is missing (unauthenticated).
    """
    path = _normalize_path(path)
    artifact_infos = await run_in_threadpool(storage_service.list_artifacts, path)

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
