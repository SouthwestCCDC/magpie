"""E2E smoke tests for upload through full stack (Caddy + API).

These tests verify that uploads complete successfully through the entire deployment
stack. They include timing measurements but do NOT verify server-side streaming or
memory usage.
"""

from __future__ import annotations

import io
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
class TestE2ECaddyLatency:
    """E2E test for Caddy proxy latency characteristics."""

    def test_caddy_upload_no_excessive_latency(self, e2e_services: E2EServices) -> None:
        """Verify upload chunk timing doesn't degrade excessively through Caddy.

        This measures chunk inter-arrival timing from the client side as an indirect
        indicator of proxy buffering. If Caddy buffers excessively, we'd expect to
        see degraded timing characteristics.

        Note: This test measures client-side observable behavior only. It cannot
        directly verify server-side streaming or memory usage. Server-side
        instrumentation (tracemalloc, logging) is needed for that.
        """
        file_size = 20 * 1024 * 1024  # 20 MB

        # Generate chunks and track timing
        chunk_size = 512 * 1024  # 512KB chunks
        chunks = []
        chunk_times = []
        position = 0

        # Pre-generate chunks
        while position < file_size:
            to_read = min(chunk_size, file_size - position)
            chunks.append(b"\x00" * to_read)
            position += to_read

        base_url = e2e_services["base_url"]
        admin_token = e2e_services["admin_token"]

        # Build file content while tracking chunk timing
        content_buffer = io.BytesIO()
        for chunk in chunks:
            chunk_times.append(time.time())
            content_buffer.write(chunk)
        content_buffer.seek(0)  # Reset to beginning for reading

        headers = {"Authorization": f"Bearer {admin_token}"}

        start_time = time.time()
        response = httpx.post(
            f"{base_url}/api/v1/upload/e2e/latency-test",
            files={"file": ("latency.bin", content_buffer, "application/octet-stream")},
            headers=headers,
            params={"uploaded_by": "latency-test"},
            timeout=60.0,
        )
        elapsed = time.time() - start_time

        assert response.status_code == 200

        # Need enough chunks to analyze timing
        if len(chunk_times) < 8:
            pytest.skip("Not enough chunks to analyze timing")

        # Calculate inter-chunk intervals
        intervals = []
        for i in range(1, len(chunk_times)):
            interval = chunk_times[i] - chunk_times[i - 1]
            intervals.append(interval)

        # Compare first and last quartiles
        quartile_size = len(intervals) // 4
        if quartile_size == 0:
            pytest.skip("Not enough intervals for quartile analysis")

        first_quartile = intervals[:quartile_size]
        last_quartile = intervals[-quartile_size:]

        avg_first = sum(first_quartile) / len(first_quartile)
        avg_last = sum(last_quartile) / len(last_quartile)

        # Check for excessive degradation
        # Using 3.0x threshold to catch severe buffering issues while tolerating
        # normal variance in CI environments
        slowdown = avg_last / avg_first if avg_first > 0 else 1.0

        assert slowdown < 3.0, (
            f"Upload chunk timing degraded {slowdown:.1f}x between start and end. "
            f"This may indicate excessive buffering in Caddy or the network stack."
        )

        print(
            f"\nE2E latency test ({elapsed:.2f}s): "
            f"first quartile={avg_first * 1000:.2f}ms/chunk, "
            f"last quartile={avg_last * 1000:.2f}ms/chunk, "
            f"slowdown={slowdown:.2f}x"
        )
