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

from magpie.auth.service import TokenInfo
from magpie.config import MagpieSettings
from magpie.server.deps import get_magpie_settings, require_admin_scope

logger = structlog.get_logger()

router = APIRouter()

# Global state for memory tracking (module-level, shared across requests)
# NOTE: These globals are not thread-safe. Memory tracking endpoints (/reset, /stats, /stop)
# must be called sequentially (one test at a time), not concurrently. This is acceptable
# since these are test-only endpoints used in controlled E2E test scenarios.
_memory_tracking_active = False
_baseline_snapshot = None
_baseline_current = 0  # Traced memory at baseline (for computing peak_delta)


class MemoryStats(BaseModel):
    """Memory tracking statistics."""

    tracking_active: bool
    peak_delta_bytes: int
    current_delta_bytes: int


@router.post("/api/v1/_test/memory/reset")
async def reset_memory_tracking(
    settings: Annotated[MagpieSettings, Depends(get_magpie_settings)],
    _admin: Annotated[TokenInfo, Depends(require_admin_scope)] = None,
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

    global _memory_tracking_active, _baseline_snapshot, _baseline_current

    # Start tracemalloc if not already running
    if not tracemalloc.is_tracing():
        tracemalloc.start()
        logger.info("memory_tracking_started", msg="tracemalloc started")

    # Take baseline snapshot and reset peak tracking for this test run
    _baseline_snapshot = tracemalloc.take_snapshot()
    tracemalloc.reset_peak()

    # Capture current traced memory after reset - this is our baseline for peak_delta
    _baseline_current, _ = tracemalloc.get_traced_memory()
    _memory_tracking_active = True

    logger.info("memory_tracking_reset", msg="Memory tracking baseline reset")

    return {"status": "reset", "message": "Memory tracking baseline established"}


@router.post("/api/v1/_test/memory/stop")
async def stop_memory_tracking(
    settings: Annotated[MagpieSettings, Depends(get_magpie_settings)],
    _admin: Annotated[TokenInfo, Depends(require_admin_scope)],
) -> dict[str, str]:
    """Stop memory tracking and clear baseline snapshot.

    This endpoint:
    1. Stops tracemalloc if it is currently running
    2. Clears the baseline snapshot
    3. Marks memory tracking as inactive

    Requires admin scope.

    Raises:
        HTTPException 403: If test endpoints are disabled.
    """
    if not settings.enable_test_endpoints:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Test endpoints are disabled in this environment",
        )

    global _memory_tracking_active, _baseline_snapshot, _baseline_current

    if tracemalloc.is_tracing():
        tracemalloc.stop()
        logger.info("memory_tracking_stopped", msg="tracemalloc stopped")

    _baseline_snapshot = None
    _baseline_current = 0
    _memory_tracking_active = False

    logger.info("memory_tracking_cleared", msg="Memory tracking state cleared")

    return {"status": "stopped", "message": "Memory tracking disabled and state cleared"}


@router.get("/api/v1/_test/memory/stats")
async def get_memory_stats(
    settings: Annotated[MagpieSettings, Depends(get_magpie_settings)],
    _admin: Annotated[TokenInfo, Depends(require_admin_scope)],
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

    # Get peak memory usage FIRST (before snapshot operations that allocate memory)
    # peak_current = current memory, peak_max = peak since last reset
    peak_current, peak_max = tracemalloc.get_traced_memory()

    # Compute peak delta: peak_max includes baseline, so subtract _baseline_current
    # reset_peak() resets to CURRENT traced memory (not zero), so we need to subtract baseline
    peak_delta = max(0, peak_max - _baseline_current)

    # Get current snapshot and compute delta (after peak measurement to avoid inflation)
    current_snapshot = tracemalloc.take_snapshot()
    current_stats = current_snapshot.compare_to(_baseline_snapshot, "lineno")

    # Calculate total memory delta (sum of all allocations minus deallocations)
    current_delta = sum(stat.size_diff for stat in current_stats)

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
