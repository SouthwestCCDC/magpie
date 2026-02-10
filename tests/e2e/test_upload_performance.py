"""E2E tests for upload performance through full stack (Caddy + API).

These tests verify that uploads stream correctly through the entire deployment
stack, catching proxy buffering issues that integration tests might miss.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

import httpx
import pytest

if TYPE_CHECKING:
    from tests.e2e.conftest import E2EServices


class StreamingUploadFile:
    """File-like object that generates data on demand for E2E upload tests."""

    def __init__(self, size: int, chunk_size: int = 65536):
        """Create streaming file.

        Args:
            size: Total size in bytes
            chunk_size: Size of chunks to generate
        """
        self.size = size
        self.chunk_size = chunk_size
        self.position = 0
        self.start_time: float | None = None
        self.chunk_times: list[tuple[int, float]] = []  # (bytes_read, timestamp)

    def read(self, size: int = -1) -> bytes:
        """Generate data on demand."""
        if self.start_time is None:
            self.start_time = time.time()

        if self.position >= self.size:
            return b""

        to_read = min(self.chunk_size, self.size - self.position)
        if size != -1 and size < to_read:
            to_read = size

        data = b"\x00" * to_read
        self.position += to_read
        self.chunk_times.append((to_read, time.time()))

        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        """Seek to position."""
        if whence == 0:
            self.position = offset
        elif whence == 1:
            self.position += offset
        elif whence == 2:
            self.position = self.size + offset
        return self.position

    def tell(self) -> int:
        """Return current position."""
        return self.position


@pytest.mark.e2e
class TestE2EUploadPerformance:
    """E2E tests for upload performance through Caddy proxy."""

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

        This catches proxy buffering issues that integration tests miss.

        Args:
            e2e_services: E2E service fixture with base_url and admin_token
            file_size_mb: File size in MB
            timeout: Maximum acceptable time in seconds
        """
        file_size = file_size_mb * 1024 * 1024
        upload_file = StreamingUploadFile(file_size)

        base_url = e2e_services["base_url"]
        admin_token = e2e_services["admin_token"]

        files = {"file": ("e2e_upload.bin", upload_file, "application/octet-stream")}
        headers = {"Authorization": f"Bearer {admin_token}"}

        start_time = time.time()
        response = httpx.post(
            f"{base_url}/api/v1/upload/e2e/perf-test",
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

        # Should complete within timeout
        assert elapsed < timeout, (
            f"E2E upload took {elapsed:.2f}s, expected < {timeout}s for {file_size_mb}MB"
        )

        # Verify streaming (file was read in multiple chunks)
        assert len(upload_file.chunk_times) > 1, "File was buffered instead of streamed"

        throughput_mbps = (file_size / (1024 * 1024)) / elapsed
        print(f"\nE2E {file_size_mb}MB: {elapsed:.2f}s ({throughput_mbps:.2f} MB/s)")

    @pytest.mark.e2e
    @pytest.mark.slow
    @pytest.mark.skipif(
        os.environ.get("MAGPIE_SKIP_LARGE_TESTS", "1") == "1",
        reason="Large tests skipped (set MAGPIE_SKIP_LARGE_TESTS=0 to enable)",
    )
    def test_large_file_e2e(self, e2e_services: E2EServices) -> None:
        """Test 500MB upload through full stack to detect buffer exhaustion.

        Skipped by default - enable with MAGPIE_SKIP_LARGE_TESTS=0.
        """
        file_size = 500 * 1024 * 1024  # 500 MB
        upload_file = StreamingUploadFile(file_size)

        base_url = e2e_services["base_url"]
        admin_token = e2e_services["admin_token"]

        files = {"file": ("e2e_large.bin", upload_file, "application/octet-stream")}
        headers = {"Authorization": f"Bearer {admin_token}"}

        start_time = time.time()
        response = httpx.post(
            f"{base_url}/api/v1/upload/e2e/large-perf",
            files=files,
            headers=headers,
            params={"uploaded_by": "e2e-large-test"},
            timeout=300.0,  # 5 minute timeout
        )
        elapsed = time.time() - start_time

        assert response.status_code == 200, f"Large E2E upload failed: {response.text}"

        # Should complete within 5 minutes
        assert elapsed < 300, f"Large E2E upload took {elapsed:.2f}s"

        # Verify streaming behavior
        assert len(upload_file.chunk_times) > 50, (
            "Large file not properly streamed through full stack"
        )

        throughput_mbps = (file_size / (1024 * 1024)) / elapsed
        print(f"\nE2E 500MB: {elapsed:.2f}s ({throughput_mbps:.2f} MB/s)")


@pytest.mark.e2e
class TestE2EUploadStability:
    """E2E tests for upload throughput stability through Caddy."""

    def test_caddy_no_buffering(self, e2e_services: E2EServices) -> None:
        """Verify Caddy doesn't buffer uploads, causing throughput degradation."""
        file_size = 20 * 1024 * 1024  # 20 MB
        upload_file = StreamingUploadFile(file_size, chunk_size=512 * 1024)  # 512KB chunks

        base_url = e2e_services["base_url"]
        admin_token = e2e_services["admin_token"]

        files = {"file": ("stability.bin", upload_file, "application/octet-stream")}
        headers = {"Authorization": f"Bearer {admin_token}"}

        response = httpx.post(
            f"{base_url}/api/v1/upload/e2e/stability",
            files=files,
            headers=headers,
            params={"uploaded_by": "stability-test"},
            timeout=60.0,
        )

        assert response.status_code == 200

        # Analyze chunk timing to detect buffering
        # Need at least 8 chunks to get meaningful quartiles (7 intervals -> quartile_size >= 1)
        if len(upload_file.chunk_times) < 8:
            pytest.skip("Not enough chunks to analyze quartiles")

        # Calculate time between chunks
        chunk_intervals = []
        for i in range(1, len(upload_file.chunk_times)):
            interval = upload_file.chunk_times[i][1] - upload_file.chunk_times[i - 1][1]
            chunk_intervals.append(interval)

        # Compare first and last quartiles
        quartile_size = len(chunk_intervals) // 4
        if quartile_size == 0:
            pytest.skip("Not enough intervals for quartile analysis")

        first_quartile = chunk_intervals[:quartile_size]
        last_quartile = chunk_intervals[-quartile_size:]

        avg_first = sum(first_quartile) / len(first_quartile)
        avg_last = sum(last_quartile) / len(last_quartile)

        # Throughput shouldn't degrade significantly
        slowdown = avg_last / avg_first if avg_first > 0 else 1.0

        assert slowdown < 2.5, (
            f"E2E upload slowed down {slowdown:.1f}x between start and end. "
            f"This suggests Caddy or FastAPI is buffering."
        )

        print(
            f"\nE2E stability: first={avg_first * 1000:.2f}ms/chunk, "
            f"last={avg_last * 1000:.2f}ms/chunk, slowdown={slowdown:.2f}x"
        )


@pytest.mark.e2e
class TestE2EMemoryBounded:
    """E2E tests to verify memory usage remains bounded during uploads."""

    def test_sequential_uploads_no_memory_accumulation(self, e2e_services: E2EServices) -> None:
        """Test that sequential uploads don't cause memory accumulation.

        This is a basic test - for more detailed memory profiling, use external tools.
        Here we just verify that multiple sequential uploads complete successfully,
        which would fail if memory was accumulating between requests.
        """
        file_size = 10 * 1024 * 1024  # 10 MB each

        base_url = e2e_services["base_url"]
        admin_token = e2e_services["admin_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Upload 3 files sequentially to test for memory accumulation between requests
        with httpx.Client(base_url=base_url, headers=headers, timeout=60.0) as client:
            upload_file1 = StreamingUploadFile(file_size)
            upload_file2 = StreamingUploadFile(file_size)
            upload_file3 = StreamingUploadFile(file_size)

            # Sequential for now (httpx Client doesn't do parallel easily)
            # If memory is unbounded, even sequential would accumulate
            responses = []
            for i, upload_file in enumerate([upload_file1, upload_file2, upload_file3], 1):
                files = {"file": (f"concurrent_{i}.bin", upload_file, "application/octet-stream")}
                response = client.post(
                    f"/api/v1/upload/e2e/concurrent-{i}",
                    files=files,
                    params={"uploaded_by": "concurrent-test"},
                )
                responses.append(response)

        # All uploads should succeed
        for i, response in enumerate(responses, 1):
            assert response.status_code == 200, f"Concurrent upload {i} failed: {response.text}"

        print("\nAll concurrent uploads completed successfully (memory bounded)")


@pytest.mark.e2e
@pytest.mark.large_upload
@pytest.mark.skipif(
    os.environ.get("MAGPIE_SKIP_LARGE_TESTS", "1") == "1",
    reason="XL tests skipped (set MAGPIE_SKIP_LARGE_TESTS=0 to enable)",
)
def test_extra_large_upload_10gb(
    e2e_services: E2EServices,
    cleanup_after_upload: list[str],
) -> None:
    """Test 10GB upload to catch buffer exhaustion issues.

    This test requires a self-hosted runner with sufficient disk space.
    Skipped on GitHub-hosted runners.

    Note: The cleanup_after_upload fixture attempts to delete artifacts but
    may fail silently (no DELETE endpoint exists). Repeated runs may accumulate
    10GB artifacts on disk. Ensure self-hosted runners have sufficient space.
    See issue #441 for artifact deletion implementation.
    """
    size_bytes = 10 * 1024 * 1024 * 1024  # 10 GB
    upload_file = StreamingUploadFile(size_bytes, chunk_size=1024 * 1024)  # 1MB chunks

    base_url = e2e_services["base_url"]
    admin_token = e2e_services["admin_token"]

    artifact_path = "e2e/xl-perf-test"
    files = {"file": ("e2e_xl_10gb.bin", upload_file, "application/octet-stream")}
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Track for cleanup
    cleanup_after_upload.append(artifact_path)

    start_time = time.time()
    response = httpx.post(
        f"{base_url}/api/v1/upload/{artifact_path}",
        files=files,
        headers=headers,
        params={"uploaded_by": "e2e-xl-test"},
        timeout=1800.0,  # 30 minute timeout
    )
    elapsed = time.time() - start_time

    assert response.status_code == 200, f"XL E2E upload failed: {response.text}"

    # Should complete within 30 minutes
    assert elapsed < 1800, f"XL E2E upload took {elapsed:.2f}s"

    # Verify streaming behavior (10GB with 1MB chunks = ~10,000 chunks)
    assert len(upload_file.chunk_times) > 100, "XL file not properly streamed through full stack"

    throughput_mbps = (size_bytes / (1024 * 1024)) / elapsed
    print(f"\nE2E 10GB: {elapsed:.2f}s ({throughput_mbps:.2f} MB/s)")
    print(f"Chunks read: {len(upload_file.chunk_times)}")
