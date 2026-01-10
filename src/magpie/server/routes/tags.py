"""Tags endpoints for global tag operations."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from magpie.server.deps import get_storage_service
from magpie.storage.service import StorageService

router = APIRouter()


class FlushTagResponse(BaseModel):
    """Response model for flush tag operation."""

    tag_name: str
    affected_artifacts: list[str]
    count: int
    dry_run: bool


@router.post("/api/v1/tags/{tag_name}/flush")
async def flush_tag(
    tag_name: str,
    confirm_walk_filesystem: Annotated[
        bool | None,
        Query(
            description="Must be true to confirm this operation walks the entire filesystem"
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        Query(description="If true, return preview without actually removing tags"),
    ] = False,
    storage_service: Annotated[StorageService, Depends(get_storage_service)] = None,
) -> FlushTagResponse:
    """Remove a tag from all artifacts globally.

    This is a potentially destructive operation that walks the entire storage
    tree and removes the specified tag from every artifact that has it.

    The confirm_walk_filesystem parameter must be explicitly set to true to
    acknowledge that this operation will scan the entire storage filesystem.

    Args:
        tag_name: Name of the tag to remove globally.
        confirm_walk_filesystem: Must be true to proceed with the operation.
        dry_run: If true, return affected artifacts without actually removing tags.

    Returns:
        FlushTagResponse with list of affected artifacts and count.

    Raises:
        HTTPException 400: If confirm_walk_filesystem is not true.
    """
    if confirm_walk_filesystem is not True:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This operation walks the entire storage filesystem. "
                "Set confirm_walk_filesystem=true to proceed."
            ),
        )

    result = storage_service.flush_tag(tag_name, dry_run=dry_run)

    return FlushTagResponse(
        tag_name=result.tag_name,
        affected_artifacts=result.affected_artifacts,
        count=result.count,
        dry_run=dry_run,
    )
