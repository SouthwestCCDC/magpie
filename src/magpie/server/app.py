"""FastAPI application for Magpie Artifacts API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from pydantic import BaseModel

from magpie import __version__
from magpie.config import get_settings
from magpie.logging_config import configure_logging
from magpie.server.errors import register_exception_handlers
from magpie.server.middleware import RequestLoggingMiddleware, VersionCheckMiddleware
from magpie.server.observability import setup_observability
from magpie.server.routes.artifacts import router as artifacts_router
from magpie.server.routes.auth import router as auth_router
from magpie.server.routes.gc import router as gc_router
from magpie.server.routes.status import router as status_router
from magpie.server.routes.tags import router as tags_router
from magpie.server.routes.test_memory import router as test_memory_router
from magpie.server.routes.upload import router as upload_router

# Configure logging at module level, before app creation,
# so logging is available during middleware initialization
configure_logging(get_settings())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan context manager for startup/shutdown events."""
    # Startup: Initialize observability (logging already configured at module level)
    settings = get_settings()
    setup_observability(app, settings)
    yield
    # Shutdown: cleanup if needed


app = FastAPI(
    title="Magpie Artifacts API",
    description="Content-addressed artifact storage with mutable tags",
    version=__version__,
    lifespan=lifespan,
)

# Add version checking middleware; it should run inside logging so that
# all requests (including 426 responses) are logged and get X-Request-ID.
# (FastAPI/Starlette runs middleware in reverse registration order)
app.add_middleware(VersionCheckMiddleware)
# Add middleware for request logging and correlation (outermost layer)
app.add_middleware(RequestLoggingMiddleware)

register_exception_handlers(app)
app.include_router(upload_router)
app.include_router(artifacts_router)
app.include_router(tags_router)
app.include_router(auth_router)
app.include_router(gc_router)
app.include_router(status_router)

# Conditionally include test endpoints (only when explicitly enabled)
settings = get_settings()
if settings.enable_test_endpoints:
    app.include_router(test_memory_router)


class HealthResponse(BaseModel):
    """Response model for health check endpoint."""

    status: str
    version: str


@app.get("/health")
async def health() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(status="ok", version=__version__)
