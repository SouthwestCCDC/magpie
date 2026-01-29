"""FastAPI application for Magpie Artifacts API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from magpie import __version__
from magpie.config import get_settings
from magpie.logging_config import configure_logging
from magpie.server.errors import register_exception_handlers
from magpie.server.middleware import RequestLoggingMiddleware
from magpie.server.observability import setup_observability
from magpie.server.routes.artifacts import router as artifacts_router
from magpie.server.routes.auth import router as auth_router
from magpie.server.routes.gc import router as gc_router
from magpie.server.routes.status import router as status_router
from magpie.server.routes.tags import router as tags_router
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

# Add middleware for request logging and correlation
app.add_middleware(RequestLoggingMiddleware)

register_exception_handlers(app)
app.include_router(upload_router)
app.include_router(artifacts_router)
app.include_router(tags_router)
app.include_router(auth_router)
app.include_router(gc_router)
app.include_router(status_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "version": __version__}
