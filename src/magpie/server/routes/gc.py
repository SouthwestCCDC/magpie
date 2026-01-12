"""GC endpoint for garbage collecting untagged blobs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from magpie.auth.service import TokenInfo
from magpie.config import MagpieSettings, get_settings
from magpie.server.deps import require_admin_scope
from magpie.server.subprocess_utils import CtlCommandError, run_ctl_command

router = APIRouter()


class GCResponse(BaseModel):
    """Response model for GC operation."""

    dry_run: bool
    artifacts_scanned: int
    blobs_found: int
    blobs_deleted: int
    space_reclaimed_bytes: int
    symlinks_checked: int
    symlinks_fixed: int
    items_removed: int = 0


@router.post("/api/v1/gc")
async def trigger_gc(
    dry_run: Annotated[bool, Query()] = False,
    retention_days_override: Annotated[int | None, Query(ge=0, alias="retention_days")] = None,
    admin: Annotated[TokenInfo, Depends(require_admin_scope)] = None,
    settings: Annotated[MagpieSettings, Depends(get_settings)] = None,
) -> GCResponse:
    """Trigger garbage collection (admin only).

    Walks all artifact directories and:
    - Identifies blobs not referenced by any tag
    - Deletes untagged blobs older than retention_days (default 90)
    - Reconciles symlinks to match manifests

    This endpoint shells out to `magpie-ctl gc` to avoid blocking the event loop.

    Args:
        dry_run: If True, preview what would be deleted without making changes.
        retention_days: Override retention period (days). Defaults to config value.
        admin: Validated admin token (injected by dependency).
        settings: Application settings (injected by dependency).

    Returns:
        GCResponse with GC operation statistics.
    """
    storage_path = settings.storage_path

    # Return empty result if storage doesn't exist yet
    if not storage_path.exists():
        return GCResponse(
            dry_run=dry_run,
            artifacts_scanned=0,
            blobs_found=0,
            blobs_deleted=0,
            space_reclaimed_bytes=0,
            symlinks_checked=0,
            symlinks_fixed=0,
            items_removed=0,
        )

    # Build command
    cmd = ["magpie-ctl", "gc", "--json-output"]
    if dry_run:
        cmd.append("--dry-run")
    if retention_days_override is not None:
        cmd.extend(["--retention-days", str(retention_days_override)])

    try:
        result = await run_ctl_command(cmd)
    except CtlCommandError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return GCResponse(
        dry_run=result.get("dry_run", dry_run),
        artifacts_scanned=result.get("artifacts_scanned", 0),
        blobs_found=result.get("blobs_found", 0),
        blobs_deleted=result.get("blobs_deleted", 0),
        space_reclaimed_bytes=result.get("space_reclaimed_bytes", 0),
        symlinks_checked=result.get("symlinks_checked", 0),
        symlinks_fixed=result.get("symlinks_fixed", 0),
        items_removed=result.get("items_removed", 0),
    )
