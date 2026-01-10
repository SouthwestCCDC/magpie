"""Error response models and exception handlers for Magpie API."""

from __future__ import annotations

import traceback
from typing import TYPE_CHECKING

from pydantic import BaseModel

from magpie.config import get_settings
from magpie.storage.exceptions import (
    ArtifactNotFoundError,
    BlobExistsError,
    HashMismatchError,
    ManifestCorruptError,
    StorageError,
)

if TYPE_CHECKING:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse


class ErrorResponse(BaseModel):
    """Structured error response for API errors."""

    error: str  # Error type (e.g., "ArtifactNotFoundError")
    message: str  # Human-readable message
    detail: dict | None = None  # Optional additional details


def _make_error_response(
    status_code: int, error_type: str, message: str, detail: dict | None = None
) -> "JSONResponse":
    """Create a JSONResponse with ErrorResponse body."""
    from fastapi.responses import JSONResponse

    body = ErrorResponse(error=error_type, message=message, detail=detail)
    return JSONResponse(status_code=status_code, content=body.model_dump())


async def artifact_not_found_handler(
    request: "Request", exc: ArtifactNotFoundError
) -> "JSONResponse":
    """Handle ArtifactNotFoundError -> HTTP 404."""
    return _make_error_response(404, "ArtifactNotFoundError", str(exc))


async def blob_exists_handler(request: "Request", exc: BlobExistsError) -> "JSONResponse":
    """Handle BlobExistsError -> HTTP 409."""
    return _make_error_response(409, "BlobExistsError", str(exc))


async def hash_mismatch_handler(request: "Request", exc: HashMismatchError) -> "JSONResponse":
    """Handle HashMismatchError -> HTTP 400."""
    return _make_error_response(400, "HashMismatchError", str(exc))


async def manifest_corrupt_handler(request: "Request", exc: ManifestCorruptError) -> "JSONResponse":
    """Handle ManifestCorruptError -> HTTP 500."""
    return _make_error_response(500, "ManifestCorruptError", str(exc))


async def storage_error_handler(request: "Request", exc: StorageError) -> "JSONResponse":
    """Handle StorageError (catch-all) -> HTTP 500."""
    return _make_error_response(500, "StorageError", str(exc))


async def generic_exception_handler(request: "Request", exc: Exception) -> "JSONResponse":
    """Handle generic exceptions -> HTTP 500.

    Includes traceback in detail if MAGPIE_DEBUG=True.
    """
    settings = get_settings()
    detail = None
    if settings.debug:
        detail = {"traceback": traceback.format_exc()}
    return _make_error_response(500, "InternalServerError", "An unexpected error occurred", detail)


def register_exception_handlers(app: "FastAPI") -> None:
    """Register all exception handlers on a FastAPI app."""
    app.add_exception_handler(ArtifactNotFoundError, artifact_not_found_handler)
    app.add_exception_handler(BlobExistsError, blob_exists_handler)
    app.add_exception_handler(HashMismatchError, hash_mismatch_handler)
    app.add_exception_handler(ManifestCorruptError, manifest_corrupt_handler)
    app.add_exception_handler(StorageError, storage_error_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
