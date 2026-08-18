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
    present but disabled (default). For actual trace verification with
    InMemorySpanExporter, see tests/unit/test_observability.py.
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

        # Tag
        # API expects ref to be either a tag name or @{short_hash} format
        hash_ref = upload_response.json()["hash_ref"]
        tag_response = authenticated_client.post(
            f"/api/v1/artifacts/{artifact_path}/{hash_ref}/tags",
            json={"tag_name": "v1.0"},
        )
        assert tag_response.status_code in (200, 201)

        # Download (uses /artifacts/ endpoint which serves files directly)
        # Blobs are stored under a truncated hash; the ref the server
        # returned is that filename, so don't hardcode the width here.
        short_hash = hash_ref.lstrip("@")
        download_response = authenticated_client.get(
            f"/artifacts/{artifact_path}/blobs/{short_hash}"
        )
        assert download_response.status_code == 200
        assert download_response.content == test_artifact_content
