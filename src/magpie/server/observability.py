"""Observability setup for Sentry and OpenTelemetry instrumentation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from fastapi import FastAPI

    from magpie.config import MagpieSettings

# Module-level flags to track initialization state
_sentry_initialized = False
_opentelemetry_initialized = False


def setup_sentry(app: "FastAPI", settings: "MagpieSettings") -> None:
    """Initialize Sentry error tracking if configured.

    This function is idempotent - calling it multiple times will only initialize
    Sentry once. Subsequent calls will be no-ops.

    Args:
        app: FastAPI application instance.
        settings: MagpieSettings with sentry_dsn configuration.
    """
    global _sentry_initialized
    logger = structlog.get_logger()

    # Check if already initialized
    if _sentry_initialized:
        logger.debug("sentry_already_initialized", reason="idempotency_guard")
        return

    if not settings.sentry_dsn:
        logger.info("sentry_disabled", reason="no_dsn")
        return

    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    environment = "development" if settings.debug else "production"

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
        ],
        environment=environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
    )

    _sentry_initialized = True
    logger.info("sentry_enabled", environment=environment)


def setup_opentelemetry(app: "FastAPI", settings: "MagpieSettings") -> None:
    """Initialize OpenTelemetry tracing if enabled.

    This function is idempotent - calling it multiple times will only initialize
    OpenTelemetry once. Subsequent calls will be no-ops.

    Args:
        app: FastAPI application instance.
        settings: MagpieSettings with otel configuration.
    """
    global _opentelemetry_initialized
    logger = structlog.get_logger()

    # Check if already initialized
    if _opentelemetry_initialized:
        logger.debug("otel_already_initialized", reason="idempotency_guard")
        return

    if not settings.otel_enabled:
        logger.info("otel_disabled")
        return

    from opentelemetry import trace
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    # Create resource with service name
    resource = Resource.create({"service.name": settings.otel_service_name})

    # Set up tracer provider
    provider = TracerProvider(resource=resource)

    # Configure OTLP exporter if endpoint is set
    if settings.otel_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        exporter = OTLPSpanExporter(endpoint=settings.otel_endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)

    # Instrument FastAPI
    FastAPIInstrumentor.instrument_app(app)

    _opentelemetry_initialized = True
    logger.info(
        "otel_enabled",
        service_name=settings.otel_service_name,
        exporter_configured=bool(settings.otel_endpoint),
    )


def setup_observability(app: "FastAPI", settings: "MagpieSettings") -> None:
    """Initialize all observability integrations.

    Args:
        app: FastAPI application instance.
        settings: MagpieSettings with observability configuration.
    """
    setup_sentry(app, settings)
    setup_opentelemetry(app, settings)


def reset_observability_state() -> None:
    """Reset the observability initialization state.

    This is primarily intended for testing purposes to allow tests to
    re-initialize observability with different configurations.

    Warning: This does not actually uninitialize Sentry or OpenTelemetry,
    it only resets the idempotency guards. Use with caution in production.
    """
    global _sentry_initialized, _opentelemetry_initialized
    _sentry_initialized = False
    _opentelemetry_initialized = False
