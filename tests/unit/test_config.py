"""Unit tests for Magpie configuration module."""

from __future__ import annotations

from pathlib import Path

import pytest

from magpie.config import MagpieSettings, get_settings


class TestMagpieSettingsDefaults:
    """Tests for default configuration values."""

    def test_default_storage_path(self) -> None:
        """Default storage_path should be /data/artifacts."""
        settings = MagpieSettings()
        assert settings.storage_path == Path("/data/artifacts")

    def test_default_retention_days(self) -> None:
        """Default retention_days should be 90."""
        settings = MagpieSettings()
        assert settings.retention_days == 90

    def test_default_debug_false(self) -> None:
        """Default debug should be False."""
        settings = MagpieSettings()
        assert settings.debug is False


class TestMagpieSettingsPathDerivation:
    """Tests for derived path computation."""

    def test_temp_path_derived_from_storage_path(self) -> None:
        """temp_path should derive from storage_path when not explicitly set."""
        settings = MagpieSettings(storage_path=Path("/custom/storage"))
        assert settings.temp_path == Path("/custom/storage/.tmp")

    def test_database_path_derived_from_storage_path(self) -> None:
        """database_path should derive from storage_path when not explicitly set."""
        settings = MagpieSettings(storage_path=Path("/custom/storage"))
        assert settings.database_path == Path("/custom/storage/.magpie.db")

    def test_default_temp_path(self) -> None:
        """Default temp_path should be {storage_path}/.tmp."""
        settings = MagpieSettings()
        assert settings.temp_path == Path("/data/artifacts/.tmp")

    def test_default_database_path(self) -> None:
        """Default database_path should be {storage_path}/.magpie.db."""
        settings = MagpieSettings()
        assert settings.database_path == Path("/data/artifacts/.magpie.db")

    def test_explicit_temp_path_not_overridden(self) -> None:
        """Explicitly set temp_path should not be overridden."""
        settings = MagpieSettings(
            storage_path=Path("/custom/storage"),
            temp_path=Path("/explicit/tmp"),
        )
        assert settings.temp_path == Path("/explicit/tmp")

    def test_explicit_database_path_not_overridden(self) -> None:
        """Explicitly set database_path should not be overridden."""
        settings = MagpieSettings(
            storage_path=Path("/custom/storage"),
            database_path=Path("/explicit/db.sqlite"),
        )
        assert settings.database_path == Path("/explicit/db.sqlite")


class TestMagpieSettingsEnvironmentOverride:
    """Tests for environment variable overrides."""

    def test_storage_path_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MAGPIE_STORAGE_PATH env var should override default."""
        monkeypatch.setenv("MAGPIE_STORAGE_PATH", "/env/storage")
        settings = MagpieSettings()
        assert settings.storage_path == Path("/env/storage")

    def test_retention_days_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MAGPIE_RETENTION_DAYS env var should override default."""
        monkeypatch.setenv("MAGPIE_RETENTION_DAYS", "30")
        settings = MagpieSettings()
        assert settings.retention_days == 30

    def test_debug_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MAGPIE_DEBUG env var should override default."""
        monkeypatch.setenv("MAGPIE_DEBUG", "true")
        settings = MagpieSettings()
        assert settings.debug is True

    def test_temp_path_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MAGPIE_TEMP_PATH env var should override derived path."""
        monkeypatch.setenv("MAGPIE_TEMP_PATH", "/env/tmp")
        settings = MagpieSettings()
        assert settings.temp_path == Path("/env/tmp")

    def test_database_path_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MAGPIE_DATABASE_PATH env var should override derived path."""
        monkeypatch.setenv("MAGPIE_DATABASE_PATH", "/env/db.sqlite")
        settings = MagpieSettings()
        assert settings.database_path == Path("/env/db.sqlite")

    def test_derived_paths_use_overridden_storage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Derived paths should use env-overridden storage_path."""
        monkeypatch.setenv("MAGPIE_STORAGE_PATH", "/env/storage")
        settings = MagpieSettings()
        assert settings.temp_path == Path("/env/storage/.tmp")
        assert settings.database_path == Path("/env/storage/.magpie.db")


class TestGetSettingsCaching:
    """Tests for get_settings() caching behavior."""

    def test_get_settings_returns_same_instance(self) -> None:
        """get_settings() should return the same cached instance."""
        get_settings.cache_clear()
        settings1 = get_settings()
        settings2 = get_settings()
        assert settings1 is settings2

    def test_get_settings_cache_clear_creates_new_instance(self) -> None:
        """Clearing cache should allow new instance creation."""
        get_settings.cache_clear()
        settings1 = get_settings()
        get_settings.cache_clear()
        settings2 = get_settings()
        assert settings1 is not settings2
