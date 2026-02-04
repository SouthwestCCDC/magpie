"""Integration tests for upload performance and streaming behavior.

These tests verify that uploads stream correctly without buffering issues.
"""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient


class FakeStreamingFile:
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


class ThroughputTrackingFile:
    """File-like object that tracks throughput over time windows."""

    def __init__(self, size: int, chunk_size: int = 1024 * 1024):
        """Create a file that tracks throughput during uploads.

        Args:
            size: Total size in bytes
            chunk_size: Size of chunks to return on each read
        """
        self.size = size
        self.chunk_size = chunk_size
        self.position = 0
        self.bytes_transferred: list[tuple[float, int]] = []  # (timestamp, bytes)
        self.start_time = time.time()

    def read(self, size: int = -1) -> bytes:
        """Read data from the fake file, tracking bytes transferred."""
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

        # Track bytes transferred with timestamp
        self.bytes_transferred.append((time.time() - self.start_time, to_read))

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
        return f"throughput_{self.size}.bin"

    def calculate_throughput_mbps(self, start_idx: int, end_idx: int) -> float:
        """Calculate throughput in MB/s for a range of transfer events.

        Args:
            start_idx: Starting index in bytes_transferred list
            end_idx: Ending index (exclusive) in bytes_transferred list

        Returns:
            Throughput in MB/s for the specified range
        """
        if start_idx >= end_idx or end_idx > len(self.bytes_transferred):
            return 0.0

        # Sum bytes transferred in this range
        total_bytes = sum(chunk[1] for chunk in self.bytes_transferred[start_idx:end_idx])

        # Calculate time window
        start_time = self.bytes_transferred[start_idx][0] if start_idx > 0 else 0.0
        end_time = self.bytes_transferred[end_idx - 1][0]
        elapsed = end_time - start_time

        if elapsed <= 0:
            return 0.0

        # Convert to MB/s
        return (total_bytes / (1024 * 1024)) / elapsed


class TestUploadThroughputStability:
    """Tests that upload throughput doesn't degrade significantly."""

    def test_throughput_does_not_degrade(self, client: TestClient) -> None:
        """Test that upload throughput remains stable throughout the transfer.

        Measures bytes/second throughput for first half vs second half of upload.
        Catches buffering issues that cause progressive slowdown.

        The production issue showed 20-40x degradation (40+ MB/s dropping to 1-2 MB/s).
        We use a 50% threshold (2x degradation) to catch severe issues while tolerating
        normal system variability.
        """
        file_size = 50 * 1024 * 1024  # 50 MB for meaningful measurement
        fake_file = ThroughputTrackingFile(file_size, chunk_size=1024 * 1024)  # 1 MB chunks

        files = {"file": ("throughput.bin", fake_file, "application/octet-stream")}

        start_time = time.time()
        response = client.post(
            "/api/v1/upload/perf/throughput",
            files=files,
            params={"uploaded_by": "throughput-test"},
        )
        elapsed = time.time() - start_time

        assert response.status_code == 200

        # Need enough transfer events to measure throughput
        if len(fake_file.bytes_transferred) < 4:
            pytest.skip("Not enough transfer events to analyze throughput")

        # Split into first half and second half
        midpoint = len(fake_file.bytes_transferred) // 2
        first_half_throughput = fake_file.calculate_throughput_mbps(0, midpoint)
        second_half_throughput = fake_file.calculate_throughput_mbps(
            midpoint, len(fake_file.bytes_transferred)
        )

        # Calculate overall throughput for reporting
        overall_throughput = (file_size / (1024 * 1024)) / elapsed

        # Assert second half throughput is at least 50% of first half
        # This catches 2x+ degradation while tolerating normal variance
        min_acceptable_throughput = first_half_throughput * 0.5

        assert second_half_throughput >= min_acceptable_throughput, (
            f"Upload throughput degraded significantly: "
            f"first half = {first_half_throughput:.2f} MB/s, "
            f"second half = {second_half_throughput:.2f} MB/s. "
            f"Second half throughput dropped below 50% of first half, "
            f"suggesting buffering issues."
        )

        # Report throughput metrics
        degradation_ratio = (
            second_half_throughput / first_half_throughput if first_half_throughput > 0 else 1.0
        )
        print(
            f"\nThroughput stability for 50MB upload ({elapsed:.2f}s):\n"
            f"  Overall: {overall_throughput:.2f} MB/s\n"
            f"  First half: {first_half_throughput:.2f} MB/s\n"
            f"  Second half: {second_half_throughput:.2f} MB/s\n"
            f"  Ratio: {degradation_ratio:.2f}x (threshold: 0.50x)"
        )
