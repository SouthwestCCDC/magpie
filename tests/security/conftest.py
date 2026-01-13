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
    return None


def _noop_require_admin_scope_header() -> None:
    """No-op override for require_admin_scope_header in tests."""
    return None


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
    """

    def override_storage_service() -> StorageService:
        return test_storage_service

    app.dependency_overrides[get_storage_service] = override_storage_service
    app.dependency_overrides[require_admin_scope] = _noop_require_admin_scope
    app.dependency_overrides[require_admin_scope_header] = _noop_require_admin_scope_header
    app.dependency_overrides[require_write_scope] = _noop_require_write_scope
    yield TestClient(app)
    app.dependency_overrides.pop(get_storage_service, None)
    app.dependency_overrides.pop(require_admin_scope, None)
    app.dependency_overrides.pop(require_admin_scope_header, None)
    app.dependency_overrides.pop(require_write_scope, None)
