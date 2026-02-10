"""Unit tests for observability setup."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
import structlog
from fastapi import FastAPI

from magpie.config import MagpieSettings
from magpie.logging_config import configure_logging
from magpie.server.observability import (
    reset_observability_state,
    setup_observability,
    setup_opentelemetry,
    setup_sentry,
)


@pytest.fixture
def test_app() -> FastAPI:
    """Create a test FastAPI app."""
    return FastAPI()


@pytest.fixture(autouse=True)
def reset_logging_for_observability():
    """Reset structlog and logging state between tests."""
    import logging

    # Reset before test
    structlog.reset_defaults()
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Configure logging with a minimal setup to prevent logger caching issues
    # This ensures that when setup_sentry()/setup_opentelemetry() call logger.info(),
    # the logger is properly configured and can be captured by monkeypatch tests
    configure_logging(MagpieSettings(log_format="json"))

    # Reset observability state to allow each test to initialize cleanly
    reset_observability_state()

    yield
    # Reset after test
    structlog.reset_defaults()
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    reset_observability_state()


class TestSentrySetup:
    """Tests for Sentry initialization."""

    def test_sentry_not_initialized_when_dsn_not_set(self, test_app: FastAPI) -> None:
        """Sentry should not be initialized when sentry_dsn is None."""
        settings = MagpieSettings(sentry_dsn=None)

        with patch("sentry_sdk.init") as mock_init:
            setup_sentry(test_app, settings)
            mock_init.assert_not_called()

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

        with (
            patch("opentelemetry.trace.set_tracer_provider") as mock_set_provider,
            patch(
                "opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app"
            ) as mock_instrument,
        ):
            setup_opentelemetry(test_app, settings)
            mock_set_provider.assert_not_called()
            mock_instrument.assert_not_called()

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

        with (
            patch("sentry_sdk.init") as mock_sentry_init,
            patch("opentelemetry.trace.set_tracer_provider") as mock_set_provider,
            patch(
                "opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app"
            ) as mock_instrument,
        ):
            setup_observability(test_app, settings)
            mock_sentry_init.assert_not_called()
            mock_set_provider.assert_not_called()
            mock_instrument.assert_not_called()

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


class TestLogging:
    """Tests for logging behavior."""

    def test_sentry_disabled_logs_message(self, test_app: FastAPI, monkeypatch) -> None:
        """Should log when Sentry is disabled."""
        settings = MagpieSettings(sentry_dsn=None, log_format="json")

        # Capture logging output
        log_stream = StringIO()
        monkeypatch.setattr("sys.stderr", log_stream)

        configure_logging(settings)
        setup_sentry(test_app, settings)

        output = log_stream.getvalue()
        assert "sentry_disabled" in output

    def test_sentry_enabled_logs_message(self, test_app: FastAPI, monkeypatch) -> None:
        """Should log when Sentry is enabled."""
        settings = MagpieSettings(sentry_dsn="https://test@sentry.io/123", log_format="json")

        # Capture logging output
        log_stream = StringIO()
        monkeypatch.setattr("sys.stderr", log_stream)

        configure_logging(settings)

        with patch("sentry_sdk.init"):
            setup_sentry(test_app, settings)

        output = log_stream.getvalue()
        assert "sentry_enabled" in output

    def test_otel_disabled_logs_message(self, test_app: FastAPI, monkeypatch) -> None:
        """Should log when OpenTelemetry is disabled."""
        settings = MagpieSettings(otel_enabled=False, log_format="json")

        # Capture logging output
        log_stream = StringIO()
        monkeypatch.setattr("sys.stderr", log_stream)

        configure_logging(settings)
        setup_opentelemetry(test_app, settings)

        output = log_stream.getvalue()
        assert "otel_disabled" in output

    def test_otel_enabled_logs_message(self, test_app: FastAPI, monkeypatch) -> None:
        """Should log when OpenTelemetry is enabled."""
        settings = MagpieSettings(otel_enabled=True, log_format="json")

        # Capture logging output
        log_stream = StringIO()
        monkeypatch.setattr("sys.stderr", log_stream)

        configure_logging(settings)

        with (
            patch("opentelemetry.trace.set_tracer_provider"),
            patch("opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app"),
        ):
            setup_opentelemetry(test_app, settings)

        output = log_stream.getvalue()
        assert "otel_enabled" in output

    def test_otel_endpoint_not_logged_verbatim(self, test_app: FastAPI, monkeypatch) -> None:
        """OTEL endpoint URL should not be logged verbatim (security concern)."""
        sensitive_endpoint = (
            "http://user:password@otel-collector.internal:4317/v1/traces?key=secret"
        )
        settings = MagpieSettings(
            otel_enabled=True, otel_endpoint=sensitive_endpoint, log_format="json"
        )

        # Capture logging output
        log_stream = StringIO()
        monkeypatch.setattr("sys.stderr", log_stream)

        configure_logging(settings)

        with (
            patch("opentelemetry.trace.set_tracer_provider"),
            patch("opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"),
            patch("opentelemetry.sdk.trace.export.BatchSpanProcessor"),
            patch("opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app"),
        ):
            setup_opentelemetry(test_app, settings)

        # Verify endpoint URL is not present in any log output
        log_output = log_stream.getvalue()
        assert sensitive_endpoint not in log_output
        assert "user:password" not in log_output
        assert "key=secret" not in log_output


class TestIdempotency:
    """Tests for idempotency of setup functions."""

    def test_setup_sentry_is_idempotent(self, test_app: FastAPI) -> None:
        """Calling setup_sentry twice should only initialize Sentry once."""
        settings = MagpieSettings(sentry_dsn="https://test@sentry.io/123")

        with patch("sentry_sdk.init") as mock_init:
            # First call should initialize
            setup_sentry(test_app, settings)
            assert mock_init.call_count == 1

            # Second call should be a no-op
            setup_sentry(test_app, settings)
            assert mock_init.call_count == 1  # Still 1, not 2

    def test_setup_opentelemetry_is_idempotent(self, test_app: FastAPI) -> None:
        """Calling setup_opentelemetry twice should only initialize once."""
        settings = MagpieSettings(otel_enabled=True)

        with (
            patch("opentelemetry.trace.set_tracer_provider") as mock_set_provider,
            patch(
                "opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app"
            ) as mock_instrument,
        ):
            # First call should initialize
            setup_opentelemetry(test_app, settings)
            assert mock_set_provider.call_count == 1
            assert mock_instrument.call_count == 1

            # Second call should be a no-op
            setup_opentelemetry(test_app, settings)
            assert mock_set_provider.call_count == 1  # Still 1, not 2
            assert mock_instrument.call_count == 1  # Still 1, not 2

    def test_setup_observability_is_idempotent(self, test_app: FastAPI) -> None:
        """Calling setup_observability twice should only initialize once."""
        settings = MagpieSettings(sentry_dsn="https://test@sentry.io/123", otel_enabled=True)

        with (
            patch("sentry_sdk.init") as mock_sentry_init,
            patch("opentelemetry.trace.set_tracer_provider"),
            patch(
                "opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app"
            ) as mock_instrument,
        ):
            # First call should initialize both
            setup_observability(test_app, settings)
            assert mock_sentry_init.call_count == 1
            assert mock_instrument.call_count == 1

            # Second call should be a no-op for both
            setup_observability(test_app, settings)
            assert mock_sentry_init.call_count == 1  # Still 1, not 2
            assert mock_instrument.call_count == 1  # Still 1, not 2

    def test_reset_observability_state_allows_reinitialization(self, test_app: FastAPI) -> None:
        """reset_observability_state should allow re-initialization."""
        settings = MagpieSettings(sentry_dsn="https://test@sentry.io/123", otel_enabled=True)

        with (
            patch("sentry_sdk.init") as mock_sentry_init,
            patch("opentelemetry.trace.set_tracer_provider"),
            patch(
                "opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument_app"
            ) as mock_instrument,
        ):
            # First initialization
            setup_observability(test_app, settings)
            assert mock_sentry_init.call_count == 1
            assert mock_instrument.call_count == 1

            # Second call without reset should be no-op
            setup_observability(test_app, settings)
            assert mock_sentry_init.call_count == 1
            assert mock_instrument.call_count == 1

            # Reset state
            reset_observability_state()

            # Third call after reset should initialize again
            setup_observability(test_app, settings)
            assert mock_sentry_init.call_count == 2
            assert mock_instrument.call_count == 2
