"""Shared fixtures for security tests.

AI-assisted: Generated with Claude Code (Opus 4.5).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magpie.auth.models import TokenScope
from magpie.auth.service import TokenInfo
from magpie.config import MagpieSettings
from magpie.server.app import app
from magpie.server.deps import (
    get_storage_service,
    require_admin_scope,
    require_admin_scope_header,
    require_read_scope,
    require_write_scope,
)
from magpie.storage.service import StorageService


def _noop_require_admin_scope() -> TokenInfo:
    """No-op override for require_admin_scope in tests."""
    return TokenInfo(
        name="test-token",
        scope=TokenScope.ADMIN,
    )


def _noop_require_write_scope() -> None:
    """No-op override for require_write_scope in tests."""


def _noop_require_admin_scope_header() -> None:
    """No-op override for require_admin_scope_header in tests."""


def _noop_require_read_scope() -> None:
    """No-op override for require_read_scope in tests."""


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with temporary paths."""
    config = MagpieSettings(storage_path=tmp_path, database_path=tmp_path / "magpie.db")
    config.temp_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def test_storage_service(test_config: MagpieSettings) -> StorageService:
    """Create a StorageService instance for testing."""
    return StorageService(test_config)


@pytest.fixture
def client(test_storage_service: StorageService, test_config: MagpieSettings) -> TestClient:
    """Create test client with overridden dependencies.

    This fixture overrides:
    - Storage service to use temporary paths
    - Magpie settings to use test config (for temp_path, etc.)
    - Auth dependencies to allow unauthenticated access (security tests
      focus on input validation, not authentication)

    Note on fixture sharing: The auth overrides here are similar to those in
    tests/integration/conftest.py. While pytest conftest.py is designed for
    fixture sharing, these test directories have different goals (integration
    vs security) and may evolve independently. Issue #113 tracks consolidating
    shared fixtures across the test suite.
    """
    from magpie.server.deps import get_magpie_settings

    # Save existing overrides to restore them after test (defensive against
    # any global state from other test modules if tests are run together)
    saved_overrides = {
        get_storage_service: app.dependency_overrides.get(get_storage_service),
        get_magpie_settings: app.dependency_overrides.get(get_magpie_settings),
        require_admin_scope: app.dependency_overrides.get(require_admin_scope),
        require_admin_scope_header: app.dependency_overrides.get(require_admin_scope_header),
        require_write_scope: app.dependency_overrides.get(require_write_scope),
        require_read_scope: app.dependency_overrides.get(require_read_scope),
    }

    def override_storage_service() -> StorageService:
        return test_storage_service

    def override_settings() -> MagpieSettings:
        return test_config

    app.dependency_overrides[get_storage_service] = override_storage_service
    app.dependency_overrides[get_magpie_settings] = override_settings
    app.dependency_overrides[require_admin_scope] = _noop_require_admin_scope
    app.dependency_overrides[require_admin_scope_header] = _noop_require_admin_scope_header
    app.dependency_overrides[require_write_scope] = _noop_require_write_scope
    app.dependency_overrides[require_read_scope] = _noop_require_read_scope
    yield TestClient(app)

    # Restore previous state (or remove if there was none)
    for dep, saved_value in saved_overrides.items():
        if saved_value is None:
            app.dependency_overrides.pop(dep, None)
        else:
            app.dependency_overrides[dep] = saved_value
