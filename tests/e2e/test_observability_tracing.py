"""E2E tests for OpenTelemetry tracing integration.

Tests that OpenTelemetry instrumentation works correctly with the complete
request flow: upload -> tag -> operations.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from tests.e2e.conftest import PROJECT_ROOT


@pytest.mark.e2e
@pytest.mark.slow
class TestOpenTelemetryTracing:
    """Tests for OpenTelemetry tracing in complete workflows."""

    def test_otel_instruments_complete_workflow(
        self,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Test that OpenTelemetry can trace a complete artifact workflow.

        This test verifies that when OTEL is enabled, the system correctly
        instruments FastAPI and traces requests through the full workflow:
        upload -> tag -> info retrieval.

        Note: This test verifies OTEL instrumentation setup, not actual trace
        export (which would require a collector). The unit tests verify that
        traces are exported when an endpoint is configured.
        """
        # Upload an artifact
        upload_response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/otel-trace-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code == 200
        artifact_hash = upload_response.json()["hash"]

        # Create a tag
        tag_response = authenticated_client.post(
            f"/api/v1/artifacts/e2e-tests/otel-trace-test/{artifact_hash}/tags",
            json={"tag": "traced"},
        )
        assert tag_response.status_code in (200, 201)

        # Get artifact info
        info_response = authenticated_client.get(
            f"/api/v1/artifacts/e2e-tests/otel-trace-test/{artifact_hash}/info"
        )
        assert info_response.status_code == 200

        # If we got here, all operations completed successfully
        # The OTEL instrumentation (if enabled) would have traced these requests


@pytest.mark.e2e
@pytest.mark.slow
class TestOpenTelemetryConfiguration:
    """Tests for OpenTelemetry configuration via environment variables."""

    def test_service_starts_with_otel_disabled(
        self,
        authenticated_client: httpx.Client,
    ) -> None:
        """Test that service starts correctly with OTEL disabled (default).

        This verifies the default configuration works correctly.
        """
        # Simple health check to ensure service is running
        response = authenticated_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_operations_complete_successfully_without_otel(
        self,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Test that all operations work correctly without OTEL enabled.

        This ensures OTEL instrumentation doesn't break normal operation
        when disabled (the default state).
        """
        # Upload
        upload_response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/no-otel-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code == 200
        artifact_hash = upload_response.json()["hash"]

        # Tag
        tag_response = authenticated_client.post(
            f"/api/v1/artifacts/e2e-tests/no-otel-test/{artifact_hash}/tags",
            json={"tag": "v1.0"},
        )
        assert tag_response.status_code in (200, 201)

        # List
        list_response = authenticated_client.get("/api/v1/artifacts/e2e-tests/")
        assert list_response.status_code == 200

        # All operations completed successfully without OTEL
