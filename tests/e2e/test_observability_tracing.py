"""E2E tests for OpenTelemetry tracing integration.

Tests that OpenTelemetry instrumentation code doesn't break the complete
request flow: upload -> tag -> download.
"""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.e2e
@pytest.mark.slow
class TestOpenTelemetryDoesNotBreakWorkflow:
    """Tests that OTEL instrumentation code doesn't break normal operation.

    Note: These tests verify the application works correctly with OTEL code
    present but disabled (default). For actual trace verification, see
    tests/unit/test_observability.py which uses InMemorySpanExporter.
    """

    def test_workflow_completes_with_otel_disabled(
        self,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify default workflow works when OTEL is disabled.

        This verifies that OTEL instrumentation doesn't break normal operation
        when disabled, and validates the default configuration.
        """
        artifact_path = "e2e-tests/no-otel-test"

        # Upload
        upload_response = authenticated_client.post(
            f"/api/v1/upload/{artifact_path}",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code == 200
        artifact_hash = upload_response.json()["hash"]

        # Tag
        tag_response = authenticated_client.post(
            f"/api/v1/artifacts/{artifact_path}/{artifact_hash}/tags",
            json={"tag": "v1.0"},
        )
        assert tag_response.status_code in (200, 201)

        # Download
        download_response = authenticated_client.get(
            f"/api/v1/artifacts/{artifact_path}/{artifact_hash}"
        )
        assert download_response.status_code == 200
        assert download_response.content == test_artifact_content
