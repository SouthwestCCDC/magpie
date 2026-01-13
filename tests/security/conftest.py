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


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with temporary paths."""
    config = MagpieSettings(storage_path=tmp_path)
    config.temp_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def test_storage_service(test_config: MagpieSettings) -> StorageService:
    """Create a StorageService instance for testing."""
    return StorageService(test_config)


@pytest.fixture
def client(test_storage_service: StorageService) -> TestClient:
    """Create test client with overridden dependencies.

    This fixture overrides:
    - Storage service to use temporary paths
    - Auth dependencies to allow unauthenticated access (security tests
      focus on input validation, not authentication)

    Note: Security tests deliberately define their own auth dependency overrides
    rather than relying on the autouse fixture in tests/integration/conftest.py.
    This provides explicit isolation - security tests run in their own test
    directory and should not depend on fixtures from other test modules. The
    duplication is intentional for clarity and to avoid cross-module coupling.

    Unlike integration tests which use an autouse fixture to override auth
    dependencies globally, this fixture explicitly manages the dependency
    lifecycle per-test. This prevents any potential conflict since:
    1. Security tests are in a separate directory (tests/security/)
    2. pytest conftest.py fixtures are scoped to their directory
    3. Each fixture saves and restores state independently
    """
    # Save existing overrides to restore them after test (defensive against
    # any global state from other test modules if tests are run together)
    saved_overrides = {
        get_storage_service: app.dependency_overrides.get(get_storage_service),
        require_admin_scope: app.dependency_overrides.get(require_admin_scope),
        require_admin_scope_header: app.dependency_overrides.get(require_admin_scope_header),
        require_write_scope: app.dependency_overrides.get(require_write_scope),
    }

    def override_storage_service() -> StorageService:
        return test_storage_service

    app.dependency_overrides[get_storage_service] = override_storage_service
    app.dependency_overrides[require_admin_scope] = _noop_require_admin_scope
    app.dependency_overrides[require_admin_scope_header] = _noop_require_admin_scope_header
    app.dependency_overrides[require_write_scope] = _noop_require_write_scope
    yield TestClient(app)

    # Restore previous state (or remove if there was none)
    for dep, saved_value in saved_overrides.items():
        if saved_value is None:
            app.dependency_overrides.pop(dep, None)
        else:
            app.dependency_overrides[dep] = saved_value
