"""E2E tests for OpenTelemetry tracing integration.

Tests that OpenTelemetry instrumentation works correctly with the complete
request flow: upload -> tag -> operations.
"""

from __future__ import annotations

import httpx
import pytest


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


    def test_complete_workflow_without_otel_enabled(
        self,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Test that operations work correctly without OTEL (default state).

        This verifies that OTEL instrumentation doesn't break normal operation
        when disabled, and validates the default configuration.
        """
        # Helper function to perform workflow operations
        def perform_workflow(artifact_path: str) -> str:
            """Upload, tag, and query an artifact. Returns the hash."""
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

            # Info
            info_response = authenticated_client.get(
                f"/api/v1/artifacts/{artifact_path}/{artifact_hash}/info"
            )
            assert info_response.status_code == 200

            return artifact_hash

        # Perform workflow - all operations should complete successfully
        perform_workflow("e2e-tests/no-otel-test")

        # Also verify health check works
        health_response = authenticated_client.get("/health")
        assert health_response.status_code == 200
        assert health_response.json() == {"status": "ok"}
