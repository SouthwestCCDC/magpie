"""Tags endpoints for global tag operations."""

from __future__ import annotations

import asyncio
import structlog
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel

from magpie.server.deps import require_admin_scope_header
from magpie.server.subprocess_utils import CtlCommandError, run_ctl_command
from magpie.validation import TAG_NAME_MAX_LENGTH, TAG_NAME_PATTERN

router = APIRouter()
logger = structlog.get_logger()


class FlushTagResponse(BaseModel):
    """Response model for flush tag operation."""

    tag_name: str
    dry_run: bool
    count: int
    affected_artifacts: list[str]


@router.post("/api/v1/tags/{tag_name}/flush")
async def flush_tag(
    tag_name: Annotated[str, Path(pattern=TAG_NAME_PATTERN, max_length=TAG_NAME_MAX_LENGTH)],
    confirm_walk_filesystem: Annotated[
        bool | None,
        Query(description="Must be true to confirm this operation walks the entire filesystem"),
    ] = None,
    dry_run: Annotated[
        bool,
        Query(description="If true, return preview without actually removing tags"),
    ] = False,
    _admin_scope_check: Annotated[None, Depends(require_admin_scope_header)] = None,
) -> FlushTagResponse:
    """Remove a tag from all artifacts globally.

    Requires admin scope. This is a potentially destructive operation that walks
    the entire storage tree and removes the specified tag from every artifact that
    has it.

    The confirm_walk_filesystem parameter must be explicitly set to true to
    acknowledge that this operation will scan the entire storage filesystem.

    This endpoint shells out to `magpie-ctl flush-tag` to avoid blocking the event loop.

    Args:
        tag_name: Name of the tag to remove globally.
        confirm_walk_filesystem: Must be true to proceed with the operation.
        dry_run: If true, return affected artifacts without actually removing tags.

    Returns:
        FlushTagResponse with list of affected artifacts and count.

    Raises:
        HTTPException 401: If X-Magpie-Scope header is missing (unauthenticated).
        HTTPException 403: If token doesn't have admin scope.
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

    # Build command
    cmd = ["magpie-ctl", "flush-tag", tag_name, "--json-output"]
    if dry_run:
        cmd.append("--dry-run")

    try:
        result = await run_ctl_command(cmd)
    except CtlCommandError as e:
        logger.error("flush_tag_failed", tag_name=tag_name, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Flush tag operation failed") from e
    except asyncio.TimeoutError:
        logger.error("flush_tag_timeout", tag_name=tag_name)
        raise HTTPException(status_code=504, detail="Operation timed out")

    return FlushTagResponse(
        tag_name=result.get("tag_name", tag_name),
        dry_run=result.get("dry_run", dry_run),
        count=result.get("count", 0),
        affected_artifacts=result.get("affected_artifacts", []),
    )
