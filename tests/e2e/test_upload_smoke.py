"""E2E smoke tests for upload through full stack (Caddy + API).

These tests verify that uploads complete successfully through the entire deployment
stack. They include timing measurements but do NOT verify server-side streaming or
memory usage.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import httpx
import pytest

if TYPE_CHECKING:
    from tests.e2e.conftest import E2EServices


@pytest.mark.e2e
class TestE2EUploadCompletion:
    """E2E smoke tests for upload through Caddy proxy."""

    @pytest.mark.parametrize(
        "file_size_mb,timeout",
        [
            (1, 15),  # 1 MB
            (50, 90),  # 50 MB
        ],
    )
    def test_full_stack_upload(
        self,
        e2e_services: E2EServices,
        file_size_mb: int,
        timeout: int,
    ) -> None:
        """Test upload through Caddy + FastAPI stack.

        This verifies the upload completes successfully through the reverse proxy.
        It does not test streaming behavior or memory usage.

        Args:
            e2e_services: E2E service fixture with base_url and admin_token
            file_size_mb: File size in MB
            timeout: Maximum acceptable time in seconds
        """
        file_size = file_size_mb * 1024 * 1024
        content = b"\x00" * file_size

        base_url = e2e_services["base_url"]
        admin_token = e2e_services["admin_token"]

        files = {"file": ("e2e_upload.bin", content, "application/octet-stream")}
        headers = {"Authorization": f"Bearer {admin_token}"}

        start_time = time.time()
        response = httpx.post(
            f"{base_url}/api/v1/upload/e2e/smoke-test",
            files=files,
            headers=headers,
            params={"uploaded_by": "e2e-test"},
            timeout=timeout,
        )
        elapsed = time.time() - start_time

        # Upload should succeed
        assert response.status_code == 200, f"E2E upload failed: {response.text}"
        data = response.json()
        assert "hash" in data

        throughput_mbps = (file_size / (1024 * 1024)) / elapsed
        print(f"\nE2E {file_size_mb}MB: {elapsed:.2f}s ({throughput_mbps:.2f} MB/s)")


@pytest.mark.e2e
class TestE2ECaddyLargeUpload:
    """E2E test for large uploads through Caddy proxy."""

    def test_caddy_large_upload_succeeds(self, e2e_services: E2EServices) -> None:
        """Verify large uploads complete successfully through Caddy reverse proxy.

        This is a straightforward smoke test that verifies the full stack
        (Caddy + FastAPI) can handle moderately large uploads without errors.

        It does NOT measure:
        - Server-side streaming behavior or memory usage
        - Actual upload latency or chunk timing
        - Proxy buffering characteristics

        For server-side validation, see issue #438 which tracks instrumentation
        for memory profiling and streaming verification.
        """
        file_size = 20 * 1024 * 1024  # 20 MB
        content = b"\x00" * file_size

        base_url = e2e_services["base_url"]
        admin_token = e2e_services["admin_token"]

        headers = {"Authorization": f"Bearer {admin_token}"}

        start_time = time.time()
        response = httpx.post(
            f"{base_url}/api/v1/upload/e2e/large-upload-test",
            files={"file": ("large.bin", content, "application/octet-stream")},
            headers=headers,
            params={"uploaded_by": "large-upload-test"},
            timeout=60.0,
        )
        elapsed = time.time() - start_time

        # Upload should succeed
        assert response.status_code == 200, f"Large upload through Caddy failed: {response.text}"
        data = response.json()
        assert "hash" in data

        throughput_mbps = (file_size / (1024 * 1024)) / elapsed
        print(f"\nE2E 20MB upload: {elapsed:.2f}s ({throughput_mbps:.2f} MB/s)")
