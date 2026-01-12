"""GC endpoint for garbage collecting untagged blobs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from magpie.auth.service import TokenInfo
from magpie.config import MagpieSettings, get_settings
from magpie.server.deps import require_admin_scope
from magpie.storage.gc import run_gc

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

    Args:
        dry_run: If True, preview what would be deleted without making changes.
        retention_days: Override retention period (days). Defaults to config value.
        admin: Validated admin token (injected by dependency).
        settings: Application settings (injected by dependency).

    Returns:
        GCResponse with GC operation statistics.
    """
    storage_path = settings.storage_path
    # Use query parameter if provided, otherwise fall back to config
    retention_days = (
        retention_days_override if retention_days_override is not None else settings.retention_days
    )

    # Run GC using shared implementation
    if not storage_path.exists():
        # Return empty result if storage doesn't exist yet
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

    result, _ = run_gc(
        storage_path=storage_path,
        retention_days=retention_days,
        dry_run=dry_run,
        reconcile_only=False,
        progress_callback=None,
    )

    return GCResponse(
        dry_run=dry_run,
        artifacts_scanned=result.artifacts_scanned,
        blobs_found=result.blobs_found,
        blobs_deleted=result.blobs_deleted,
        space_reclaimed_bytes=result.space_reclaimed_bytes,
        symlinks_checked=result.symlinks_checked,
        symlinks_fixed=result.symlinks_fixed,
        items_removed=result.items_removed,
    )
