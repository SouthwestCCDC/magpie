"""Unit tests for FastAPI dependency injection functions."""

from __future__ import annotations

from pathlib import Path

import pytest

from magpie.config import get_settings
from magpie.server.deps import clear_token_service_cache, get_token_service


class TestTokenServiceCaching:
    """Tests for get_token_service() caching behavior."""

    def test_get_token_service_returns_same_instance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_token_service() should return the same cached instance."""
        # Set up temporary database path
        monkeypatch.setenv("MAGPIE_DATABASE_PATH", str(tmp_path / "test.db"))
        get_settings.cache_clear()

        clear_token_service_cache()
        service1 = get_token_service()
        service2 = get_token_service()
        assert service1 is service2

    def test_clear_token_service_cache_creates_new_instance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Clearing cache should allow new instance creation."""
        # Set up temporary database path
        monkeypatch.setenv("MAGPIE_DATABASE_PATH", str(tmp_path / "test.db"))
        get_settings.cache_clear()

        clear_token_service_cache()
        service1 = get_token_service()
        clear_token_service_cache()
        service2 = get_token_service()
        assert service1 is not service2
