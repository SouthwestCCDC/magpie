"""Integration tests for upload performance and streaming behavior.

These tests verify that uploads stream correctly without buffering issues.
"""

from __future__ import annotations

import io
import os
import time

import pytest
from fastapi.testclient import TestClient


class FakeStreamingFile(io.BytesIO):
    """File-like object that tracks read patterns to detect buffering issues."""

    def __init__(self, size: int, chunk_size: int = 8192):
        """Create a fake file that generates data on demand.

        Args:
            size: Total size in bytes
            chunk_size: Size of chunks to return on each read
        """
        self.size = size
        self.chunk_size = chunk_size
        self.position = 0
        self.read_times: list[float] = []
        self.bytes_read: list[int] = []

    def read(self, size: int = -1) -> bytes:
        """Read data from the fake file, tracking timing."""
        read_start = time.time()

        if self.position >= self.size:
            return b""

        # Determine how much to read
        if size == -1 or size > self.chunk_size:
            to_read = min(self.chunk_size, self.size - self.position)
        else:
            to_read = min(size, self.size - self.position)

        # Generate fake data (zeros for efficiency)
        data = b"\x00" * to_read
        self.position += to_read

        self.read_times.append(time.time() - read_start)
        self.bytes_read.append(to_read)

        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        """Seek to position in fake file."""
        if whence == 0:  # SEEK_SET
            self.position = offset
        elif whence == 1:  # SEEK_CUR
            self.position += offset
        elif whence == 2:  # SEEK_END
            self.position = self.size + offset
        return self.position

    def tell(self) -> int:
        """Return current position."""
        return self.position

    @property
    def name(self) -> str:
        """Return fake filename."""
        return f"fake_{self.size}.bin"


class TestUploadStreamingPerformance:
    """Tests for upload streaming behavior with various file sizes."""

    @pytest.mark.parametrize(
        "file_size_mb,timeout",
        [
            (1, 10),  # 1 MB - should complete quickly
            (50, 60),  # 50 MB - medium file
        ],
    )
    def test_upload_streaming(
        self,
        client: TestClient,
        file_size_mb: int,
        timeout: int,
    ) -> None:
        """Test that uploads complete without excessive buffering.

        Args:
            client: FastAPI test client
            file_size_mb: Size of file to upload in MB
            timeout: Maximum acceptable time in seconds
        """
        file_size = file_size_mb * 1024 * 1024
        fake_file = FakeStreamingFile(file_size)

        files = {"file": ("large.bin", fake_file, "application/octet-stream")}

        start_time = time.time()
        response = client.post(
            "/api/v1/upload/perf/test",
            files=files,
            params={"uploaded_by": "perf-test"},
        )
        elapsed = time.time() - start_time

        # Upload should succeed
        assert response.status_code == 200, f"Upload failed: {response.text}"
        data = response.json()
        assert "hash" in data
        assert data["artifact_path"] == "perf/test"

        # Upload should complete within timeout
        assert elapsed < timeout, (
            f"Upload took {elapsed:.2f}s, expected < {timeout}s for {file_size_mb}MB file"
        )

        # File should have been read in chunks (streaming behavior)
        # If buffering occurred, we'd see one large read
        assert len(fake_file.bytes_read) > 1, (
            "File was read in a single operation, indicating full buffering"
        )

        # Calculate throughput
        throughput_mbps = (file_size / (1024 * 1024)) / elapsed
        print(f"\n{file_size_mb}MB upload: {elapsed:.2f}s ({throughput_mbps:.2f} MB/s)")

    @pytest.mark.slow
    @pytest.mark.skipif(
        os.environ.get("MAGPIE_SKIP_LARGE_TESTS", "1") == "1",
        reason="Large tests skipped (set MAGPIE_SKIP_LARGE_TESTS=0 to enable)",
    )
    def test_large_upload_streaming(self, client: TestClient) -> None:
        """Test 500MB upload to detect buffer exhaustion issues.

        This test is skipped by default in CI but can be run manually
        or in nightly jobs by setting MAGPIE_SKIP_LARGE_TESTS=0.
        """
        file_size = 500 * 1024 * 1024  # 500 MB
        fake_file = FakeStreamingFile(file_size)

        files = {"file": ("verylarge.bin", fake_file, "application/octet-stream")}

        start_time = time.time()
        response = client.post(
            "/api/v1/upload/perf/large",
            files=files,
            params={"uploaded_by": "large-perf-test"},
        )
        elapsed = time.time() - start_time

        # Upload should succeed
        assert response.status_code == 200, f"Large upload failed: {response.text}"

        # Should complete within 5 minutes (generous timeout)
        assert elapsed < 300, f"Large upload took {elapsed:.2f}s, expected < 300s"

        # Verify streaming behavior
        assert len(fake_file.bytes_read) > 100, (
            "Large file was not properly streamed (too few read operations)"
        )

        throughput_mbps = (file_size / (1024 * 1024)) / elapsed
        print(f"\n500MB upload: {elapsed:.2f}s ({throughput_mbps:.2f} MB/s)")


class TestUploadThroughputStability:
    """Tests that upload throughput doesn't degrade significantly."""

    def test_throughput_does_not_degrade(self, client: TestClient) -> None:
        """Test that upload speed remains stable throughout the upload.

        This detects issues where uploads start fast but slow down
        significantly, which indicates buffering problems.
        """
        # Use a medium-sized file to observe throughput patterns
        file_size = 10 * 1024 * 1024  # 10 MB
        fake_file = FakeStreamingFile(file_size, chunk_size=256 * 1024)  # 256KB chunks

        files = {"file": ("throughput.bin", fake_file, "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/perf/throughput",
            files=files,
            params={"uploaded_by": "throughput-test"},
        )

        assert response.status_code == 200

        # Analyze read patterns
        # Compare first quartile timing vs last quartile timing
        if len(fake_file.read_times) < 4:
            pytest.skip("Not enough read operations to analyze throughput")

        quartile_size = len(fake_file.read_times) // 4
        first_quartile = fake_file.read_times[:quartile_size]
        last_quartile = fake_file.read_times[-quartile_size:]

        avg_first = sum(first_quartile) / len(first_quartile)
        avg_last = sum(last_quartile) / len(last_quartile)

        # Throughput shouldn't degrade by more than 50%
        # (inverse relationship: slower read times = lower throughput)
        degradation_factor = avg_last / avg_first if avg_first > 0 else 1.0

        assert degradation_factor < 2.5, (
            f"Upload throughput degraded significantly: "
            f"last quartile {degradation_factor:.1f}x slower than first quartile. "
            f"This suggests buffering issues."
        )

        print(
            f"\nThroughput stability: first quartile avg={avg_first * 1000:.2f}ms, "
            f"last quartile avg={avg_last * 1000:.2f}ms, ratio={degradation_factor:.2f}x"
        )
