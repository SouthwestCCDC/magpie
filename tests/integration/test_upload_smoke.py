"""Integration tests for upload smoke testing.

These tests verify that uploads of various sizes complete successfully.
They do NOT test server-side streaming, memory usage, or throughput.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestUploadCompletion:
    """Smoke tests that verify uploads complete successfully."""

    @pytest.mark.parametrize(
        "file_size_mb",
        [
            1,  # 1 MB
            50,  # 50 MB
        ],
    )
    def test_upload_completes(
        self,
        client: TestClient,
        file_size_mb: int,
    ) -> None:
        """Test that uploads of various sizes complete successfully.

        This is a basic smoke test - it verifies the upload endpoint accepts
        and stores the data, but does not test streaming behavior, memory usage,
        or performance characteristics.

        Args:
            client: FastAPI test client
            file_size_mb: Size of file to upload in MB
        """
        file_size = file_size_mb * 1024 * 1024
        content = b"\x00" * file_size

        files = {"file": ("large.bin", content, "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/smoke/test",
            files=files,
            params={"uploaded_by": "smoke-test"},
        )

        # Upload should succeed
        assert response.status_code == 200, f"Upload failed: {response.text}"
        data = response.json()
        assert "hash" in data
        assert data["artifact_path"] == "smoke/test"

        print(f"\n{file_size_mb}MB upload completed successfully")
