"""Performance benchmarks for TokenService instantiation and caching.

These tests verify that the lru_cache optimization on get_token_service()
effectively prevents repeated database initialization overhead.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from magpie.auth.database import init_database
from magpie.config import get_settings
from magpie.server.deps import clear_token_service_cache, get_token_service


class TestTokenServicePerformance:
    """Benchmark tests for TokenService caching optimization."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set up test environment with temporary database."""
        monkeypatch.setenv("MAGPIE_DATABASE_PATH", str(tmp_path / "test.db"))
        get_settings.cache_clear()
        clear_token_service_cache()
        yield
        clear_token_service_cache()

    def test_init_database_called_once_with_caching(self) -> None:
        """Verify init_database is called only once when using cached get_token_service().

        This test confirms the optimization from issue #426: with lru_cache,
        init_database() is only called on the first get_token_service() call,
        not on every request.
        """
        with patch("magpie.auth.service.init_database", wraps=init_database) as mock_init:
            # Simulate multiple requests (each would call get_token_service)
            for _ in range(10):
                service = get_token_service()
                # Use the service to ensure it's fully initialized
                _ = service.db_path

            # With caching, init_database should only be called once
            # (when TokenService.__init__ runs the first time)
            assert mock_init.call_count == 1

    def test_init_database_called_multiple_times_without_caching(self) -> None:
        """Verify init_database would be called repeatedly without caching.

        This demonstrates the problem that issue #426 addressed: without caching,
        each TokenService instantiation calls init_database().
        """
        from magpie.auth.service import TokenService

        with patch("magpie.auth.service.init_database", wraps=init_database) as mock_init:
            settings = get_settings()

            # Simulate the old behavior: creating a new instance per request
            for _ in range(10):
                service = TokenService(settings)
                _ = service.db_path

            # Without caching, init_database is called on every instantiation
            assert mock_init.call_count == 10

    def test_cached_instance_access_is_fast(self) -> None:
        """Benchmark cached vs uncached TokenService access.

        Verifies that cached access is significantly faster than creating
        new instances. This test quantifies the performance benefit of the
        lru_cache optimization.
        """
        settings = get_settings()

        # Warm up: create the first instance (includes DB initialization)
        clear_token_service_cache()
        get_token_service()

        # Benchmark: cached access (should be very fast)
        iterations = 1000
        start_cached = time.perf_counter()
        for _ in range(iterations):
            get_token_service()
        cached_time = time.perf_counter() - start_cached

        # Benchmark: uncached access (creates new instance each time)
        from magpie.auth.service import TokenService

        start_uncached = time.perf_counter()
        for _ in range(iterations):
            TokenService(settings)
        uncached_time = time.perf_counter() - start_uncached

        # Cached access should be at least 10x faster
        # (typically 100-1000x faster in practice)
        assert cached_time < uncached_time / 10, (
            f"Cached access ({cached_time:.4f}s) should be significantly faster "
            f"than uncached ({uncached_time:.4f}s)"
        )

        # Print benchmark results for visibility
        print(
            f"\nTokenService instantiation benchmark ({iterations} iterations):\n"
            f"  Cached (lru_cache):   {cached_time:.4f}s ({cached_time / iterations * 1000000:.2f}µs per call)\n"
            f"  Uncached (new instance): {uncached_time:.4f}s ({uncached_time / iterations * 1000000:.2f}µs per call)\n"
            f"  Speedup: {uncached_time / cached_time:.1f}x"
        )
