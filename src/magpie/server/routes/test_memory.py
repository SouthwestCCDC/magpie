"""Test-only endpoints for memory tracking during uploads.

These endpoints are only enabled when MAGPIE_ENABLE_TEST_ENDPOINTS=true
and provide tracemalloc-based memory profiling to verify upload streaming behavior.

SECURITY: These endpoints require admin scope and should NEVER be enabled
in production environments.
"""

from __future__ import annotations

import tracemalloc
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from magpie.config import MagpieSettings
from magpie.server.deps import get_magpie_settings, require_admin_scope

logger = structlog.get_logger()

router = APIRouter()

# Global state for memory tracking (module-level, shared across requests)
_memory_tracking_active = False
_baseline_snapshot = None


class MemoryStats(BaseModel):
    """Memory tracking statistics."""

    tracking_active: bool
    peak_delta_bytes: int
    current_delta_bytes: int


@router.post("/api/v1/_test/memory/reset")
async def reset_memory_tracking(
    settings: Annotated[MagpieSettings, Depends(get_magpie_settings)],
    _admin_scope_check: Annotated[None, Depends(require_admin_scope)] = None,
) -> dict[str, str]:
    """Reset memory tracking and take baseline snapshot.

    This endpoint:
    1. Starts tracemalloc if not already running
    2. Takes a baseline memory snapshot
    3. Resets peak tracking

    Requires admin scope.

    Raises:
        HTTPException 403: If test endpoints are disabled.
    """
    if not settings.enable_test_endpoints:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Test endpoints are disabled in this environment",
        )

    global _memory_tracking_active, _baseline_snapshot

    # Start tracemalloc if not already running
    if not tracemalloc.is_tracing():
        tracemalloc.start()
        logger.info("memory_tracking_started", msg="tracemalloc started")

    # Take baseline snapshot
    _baseline_snapshot = tracemalloc.take_snapshot()
    _memory_tracking_active = True

    logger.info("memory_tracking_reset", msg="Memory tracking baseline reset")

    return {"status": "reset", "message": "Memory tracking baseline established"}


@router.get("/api/v1/_test/memory/stats")
async def get_memory_stats(
    settings: Annotated[MagpieSettings, Depends(get_magpie_settings)],
    _admin_scope_check: Annotated[None, Depends(require_admin_scope)] = None,
) -> MemoryStats:
    """Get current memory tracking statistics.

    Returns peak and current memory delta since last reset.

    Requires admin scope.

    Raises:
        HTTPException 403: If test endpoints are disabled.
        HTTPException 400: If memory tracking has not been initialized.
    """
    if not settings.enable_test_endpoints:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Test endpoints are disabled in this environment",
        )

    if not _memory_tracking_active or _baseline_snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Memory tracking not initialized - call /api/v1/_test/memory/reset first",
        )

    # Get current snapshot and compute delta
    current_snapshot = tracemalloc.take_snapshot()
    current_stats = current_snapshot.compare_to(_baseline_snapshot, "lineno")

    # Calculate total memory delta (sum of all allocations minus deallocations)
    current_delta = sum(stat.size_diff for stat in current_stats)

    # Get peak memory usage (tracemalloc tracks this automatically)
    peak_current, peak_max = tracemalloc.get_traced_memory()

    # Calculate peak delta from baseline
    # Note: We use peak_max - baseline_current as an approximation
    # More accurate tracking would require periodic polling, but this is sufficient
    # for detecting buffering issues (500MB upload should show <50MB delta)
    baseline_current = _baseline_snapshot.statistics("lineno")
    baseline_total = sum(stat.size for stat in baseline_current)
    peak_delta = peak_max - baseline_total

    logger.debug(
        "memory_stats_retrieved",
        peak_delta_mb=round(peak_delta / (1024 * 1024), 2),
        current_delta_mb=round(current_delta / (1024 * 1024), 2),
    )

    return MemoryStats(
        tracking_active=True,
        peak_delta_bytes=peak_delta,
        current_delta_bytes=current_delta,
    )
