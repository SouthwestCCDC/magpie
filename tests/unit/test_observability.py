"""Unit tests for observability setup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI

from magpie.config import MagpieSettings
from magpie.server.observability import (
    setup_observability,
    setup_opentelemetry,
    setup_sentry,
)


@pytest.fixture
def test_app() -> FastAPI:
    """Create a test FastAPI app."""
    return FastAPI()


class TestSentrySetup:
    """Tests for Sentry initialization."""

    def test_sentry_not_initialized_when_dsn_not_set(self, test_app: FastAPI) -> None:
        """Sentry should not be initialized when sentry_dsn is None."""
        settings = MagpieSettings(sentry_dsn=None)

        # When DSN is None, function should return early without importing sentry_sdk
        # We verify this by checking no exception is raised and no side effects occur
        with patch.dict("sys.modules", {"sentry_sdk": MagicMock()}):
            setup_sentry(test_app, settings)
            # sentry_sdk should not have been imported since we return early
            # The function returns before importing, so this should succeed

    def test_sentry_initialized_when_dsn_set(self, test_app: FastAPI) -> None:
        """Sentry should be initialized when sentry_dsn is provided."""
        settings = MagpieSettings(sentry_dsn="https://test@sentry.io/123")

        with patch("sentry_sdk.init") as mock_init:
            setup_sentry(test_app, settings)
            mock_init.assert_called_once()

    def test_sentry_uses_production_environment_when_not_debug(self, test_app: FastAPI) -> None:
        """Sentry should use 'production' environment when debug is False."""
        settings = MagpieSettings(sentry_dsn="https://test@sentry.io/123", debug=False)

        with patch("sentry_sdk.init") as mock_init:
            setup_sentry(test_app, settings)
            call_kwargs = mock_init.call_args.kwargs
            assert call_kwargs["environment"] == "production"

    def test_sentry_uses_development_environment_when_debug(self, test_app: FastAPI) -> None:
        """Sentry should use 'development' environment when debug is True."""
        settings = MagpieSettings(sentry_dsn="https://test@sentry.io/123", debug=True)

        with patch("sentry_sdk.init") as mock_init:
            setup_sentry(test_app, settings)
            call_kwargs = mock_init.call_args.kwargs
            assert call_kwargs["environment"] == "development"


class TestOpenTelemetrySetup:
    """Tests for OpenTelemetry initialization."""

    def test_otel_not_initialized_when_disabled(self, test_app: FastAPI) -> None:
        """OpenTelemetry should not be initialized when otel_enabled is False."""
        settings = MagpieSettings(otel_enabled=False)

        # When disabled, function returns early without importing anything
        # Just verify function completes without error
        setup_opentelemetry(test_app, settings)
        # If we get here without error, the early return worked

    def test_otel_initialized_when_enabled(self, test_app: FastAPI) -> None:
        """OpenTelemetry should be initialized when otel_enabled is True."""
        settings = MagpieSettings(otel_enabled=True)

        with (
            patch("opentelemetry.trace.set_tracer_provider"),
            patch(
                "opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app"
            ) as mock_instrument,
        ):
            setup_opentelemetry(test_app, settings)
            mock_instrument.assert_called_once_with(test_app)

    def test_otel_uses_configured_service_name(self, test_app: FastAPI) -> None:
        """OpenTelemetry should use the configured service name."""
        settings = MagpieSettings(otel_enabled=True, otel_service_name="custom-service")

        with (
            patch("opentelemetry.trace.set_tracer_provider"),
            patch("opentelemetry.sdk.resources.Resource.create") as mock_resource,
            patch("opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app"),
        ):
            setup_opentelemetry(test_app, settings)
            mock_resource.assert_called_once_with({"service.name": "custom-service"})

    def test_otel_configures_otlp_exporter_when_endpoint_set(self, test_app: FastAPI) -> None:
        """OTLP exporter should be configured when otel_endpoint is set."""
        settings = MagpieSettings(otel_enabled=True, otel_endpoint="http://localhost:4317")

        with (
            patch("opentelemetry.trace.set_tracer_provider"),
            patch(
                "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"
            ) as mock_exporter,
            patch("opentelemetry.sdk.trace.export.BatchSpanProcessor"),
            patch("opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app"),
        ):
            setup_opentelemetry(test_app, settings)
            mock_exporter.assert_called_once_with(endpoint="http://localhost:4317")

    def test_otel_no_exporter_when_endpoint_not_set(self, test_app: FastAPI) -> None:
        """OTLP exporter should not be configured when otel_endpoint is None."""
        settings = MagpieSettings(otel_enabled=True, otel_endpoint=None)

        with (
            patch("opentelemetry.trace.set_tracer_provider"),
            patch(
                "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"
            ) as mock_exporter,
            patch("opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app"),
        ):
            setup_opentelemetry(test_app, settings)
            mock_exporter.assert_not_called()


class TestConfigToggles:
    """Tests for configuration toggles."""

    def test_setup_observability_respects_all_toggles_disabled(self, test_app: FastAPI) -> None:
        """setup_observability should not initialize anything when all disabled."""
        settings = MagpieSettings(sentry_dsn=None, otel_enabled=False)

        # Both are disabled, so function should complete without importing SDKs
        setup_observability(test_app, settings)
        # If we get here without error, early returns worked

    def test_setup_observability_initializes_both_when_configured(self, test_app: FastAPI) -> None:
        """setup_observability should initialize both when both are configured."""
        settings = MagpieSettings(sentry_dsn="https://test@sentry.io/123", otel_enabled=True)

        with (
            patch("sentry_sdk.init") as mock_sentry_init,
            patch("opentelemetry.trace.set_tracer_provider"),
            patch(
                "opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app"
            ) as mock_instrument,
        ):
            setup_observability(test_app, settings)
            mock_sentry_init.assert_called_once()
            mock_instrument.assert_called_once()

    def test_default_settings_disable_observability(self) -> None:
        """Default settings should have observability disabled."""
        settings = MagpieSettings()

        assert settings.sentry_dsn is None
        assert settings.otel_enabled is False
        assert settings.otel_endpoint is None
        assert settings.otel_service_name == "magpie"
