"""Unit tests for Magpie configuration module."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

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

    def test_default_gc_lock_path(self) -> None:
        """Default gc_lock_path should be /var/run/magpie-gc.lock."""
        settings = MagpieSettings()
        assert settings.gc_lock_path == Path("/var/run/magpie-gc.lock")

    def test_default_max_upload_size_none(self) -> None:
        """Default max_upload_size should be None (no limit)."""
        settings = MagpieSettings()
        assert settings.max_upload_size is None

    def test_default_s3_bucket_none(self) -> None:
        """Default s3_bucket should be None (disabled)."""
        settings = MagpieSettings()
        assert settings.s3_bucket is None

    def test_default_log_format_json(self) -> None:
        """Default log_format should be 'json' for production use."""
        settings = MagpieSettings()
        assert settings.log_format == "json"


class TestMagpieSettingsPathDerivation:
    """Tests for derived path computation."""

    def test_temp_path_derived_from_storage_path(self) -> None:
        """temp_path should derive from storage_path when not explicitly set."""
        settings = MagpieSettings(storage_path=Path("/custom/storage"))
        assert settings.temp_path == Path("/custom/storage/.tmp")

    def test_default_temp_path(self) -> None:
        """Default temp_path should be {storage_path}/.tmp."""
        settings = MagpieSettings()
        assert settings.temp_path == Path("/data/artifacts/.tmp")

    def test_default_database_path(self) -> None:
        """Default database_path should be /data/magpie.db."""
        settings = MagpieSettings()
        assert settings.database_path == Path("/data/magpie.db")

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
        """temp_path should use env-overridden storage_path."""
        monkeypatch.setenv("MAGPIE_STORAGE_PATH", "/env/storage")
        settings = MagpieSettings()
        assert settings.temp_path == Path("/env/storage/.tmp")
        # database_path has its own default, not derived from storage_path
        assert settings.database_path == Path("/data/magpie.db")

    def test_gc_lock_path_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MAGPIE_GC_LOCK_PATH env var should override default."""
        monkeypatch.setenv("MAGPIE_GC_LOCK_PATH", "/custom/gc.lock")
        settings = MagpieSettings()
        assert settings.gc_lock_path == Path("/custom/gc.lock")

    def test_max_upload_size_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MAGPIE_MAX_UPLOAD_SIZE env var should override default."""
        monkeypatch.setenv("MAGPIE_MAX_UPLOAD_SIZE", "1073741824")
        settings = MagpieSettings()
        assert settings.max_upload_size == 1073741824

    def test_s3_bucket_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MAGPIE_S3_BUCKET env var should override default."""
        monkeypatch.setenv("MAGPIE_S3_BUCKET", "my-backup-bucket")
        settings = MagpieSettings()
        assert settings.s3_bucket == "my-backup-bucket"

    def test_log_format_env_override_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MAGPIE_LOG_FORMAT env var should accept 'json'."""
        monkeypatch.setenv("MAGPIE_LOG_FORMAT", "json")
        settings = MagpieSettings()
        assert settings.log_format == "json"

    def test_log_format_env_override_console(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MAGPIE_LOG_FORMAT env var should accept 'console'."""
        monkeypatch.setenv("MAGPIE_LOG_FORMAT", "console")
        settings = MagpieSettings()
        assert settings.log_format == "console"

    def test_log_format_invalid_value_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid MAGPIE_LOG_FORMAT values should raise ValidationError."""
        monkeypatch.setenv("MAGPIE_LOG_FORMAT", "invalid")
        with pytest.raises(ValidationError):
            MagpieSettings()


class TestMagpieSettingsCIDRValidation:
    """Tests for CIDR validation (issue #230)."""

    def test_empty_cidrs_allowed(self) -> None:
        """Empty allowed_cidrs should be valid (default case)."""
        settings = MagpieSettings(allowed_cidrs="")
        assert settings.allowed_cidrs == ""

    def test_valid_single_cidr(self) -> None:
        """Single valid CIDR should be accepted."""
        settings = MagpieSettings(allowed_cidrs="10.0.0.0/8")
        assert settings.allowed_cidrs == "10.0.0.0/8"

    def test_valid_multiple_cidrs(self) -> None:
        """Multiple valid CIDRs should be accepted (space-separated)."""
        settings = MagpieSettings(allowed_cidrs="10.0.0.0/8 192.168.1.0/24")
        assert settings.allowed_cidrs == "10.0.0.0/8 192.168.1.0/24"

    def test_valid_cidrs_with_extra_spaces(self) -> None:
        """CIDRs with extra spaces should be accepted (split handles multiple spaces)."""
        settings = MagpieSettings(allowed_cidrs="10.0.0.0/8  192.168.1.0/24")
        assert settings.allowed_cidrs == "10.0.0.0/8  192.168.1.0/24"

    def test_valid_ipv6_cidr(self) -> None:
        """IPv6 CIDRs should be accepted."""
        settings = MagpieSettings(allowed_cidrs="2001:db8::/32")
        assert settings.allowed_cidrs == "2001:db8::/32"

    def test_valid_mixed_ipv4_ipv6(self) -> None:
        """Mixed IPv4 and IPv6 CIDRs should be accepted (space-separated)."""
        settings = MagpieSettings(allowed_cidrs="10.0.0.0/8 2001:db8::/32")
        assert settings.allowed_cidrs == "10.0.0.0/8 2001:db8::/32"

    def test_invalid_cidr_notation_raises(self) -> None:
        """Invalid CIDR notation should raise ValidationError."""
        with pytest.raises(ValidationError, match="Invalid CIDR notation"):
            MagpieSettings(allowed_cidrs="10.0.0.0/99")

    def test_invalid_ip_address_raises(self) -> None:
        """Invalid IP address should raise ValidationError."""
        with pytest.raises(ValidationError, match="Invalid CIDR notation"):
            MagpieSettings(allowed_cidrs="999.999.999.999/24")

    def test_malformed_cidr_raises(self) -> None:
        """Malformed CIDR (no slash) should raise ValidationError."""
        with pytest.raises(ValidationError, match="Invalid CIDR notation"):
            MagpieSettings(allowed_cidrs="not-a-cidr")

    def test_cidr_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MAGPIE_ALLOWED_CIDRS env var should override default (space-separated)."""
        monkeypatch.setenv("MAGPIE_ALLOWED_CIDRS", "10.0.0.0/8 192.168.1.0/24")
        settings = MagpieSettings()
        assert settings.allowed_cidrs == "10.0.0.0/8 192.168.1.0/24"

    def test_cidr_env_override_validates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid CIDR in env var should raise ValidationError."""
        monkeypatch.setenv("MAGPIE_ALLOWED_CIDRS", "invalid-cidr")
        with pytest.raises(ValidationError, match="Invalid CIDR notation"):
            MagpieSettings()


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
