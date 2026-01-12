"""Integration tests for the flush tag endpoint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from magpie.config import MagpieSettings
from magpie.server.app import app


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with temporary paths."""
    config = MagpieSettings(storage_path=tmp_path)
    config.temp_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def client() -> TestClient:
    """Create test client."""
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


class TestDryRunPreview:
    """Tests for dry_run mode returning preview without modification."""

    def test_dry_run_returns_preview(self, client: TestClient) -> None:
        """Dry run returns affected artifacts."""
        mock_result = {
            "tag": "release",
            "dry_run": True,
            "artifacts_affected": 2,
            "artifacts": ["flush-test/artifact1", "flush-test/artifact2"],
        }

        with patch(
            "magpie.server.routes.tags.run_ctl_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = client.post(
                "/api/v1/tags/release/flush",
                params={"confirm_walk_filesystem": True, "dry_run": True},
            )

        assert response.status_code == 200
        data = response.json()

        assert data["tag"] == "release"
        assert data["artifacts_affected"] == 2
        assert data["dry_run"] is True
        assert "flush-test/artifact1" in data["artifacts"]
        assert "flush-test/artifact2" in data["artifacts"]


class TestConfirmedFlush:
    """Tests for confirmed flush."""

    def test_confirmed_flush_calls_subprocess(self, client: TestClient) -> None:
        """Confirmed flush calls subprocess and returns result."""
        mock_result = {
            "tag": "to-flush",
            "dry_run": False,
            "artifacts_affected": 2,
            "artifacts": ["flush-confirm/art1", "flush-confirm/art2"],
        }

        with patch(
            "magpie.server.routes.tags.run_ctl_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = client.post(
                "/api/v1/tags/to-flush/flush",
                params={"confirm_walk_filesystem": True, "dry_run": False},
            )

        assert response.status_code == 200
        data = response.json()

        assert data["tag"] == "to-flush"
        assert data["artifacts_affected"] == 2
        assert data["dry_run"] is False


class TestMissingConfirmation:
    """Tests for missing confirmation parameter."""

    def test_missing_confirmation_returns_400(self, client: TestClient) -> None:
        """Missing confirm_walk_filesystem parameter returns 400."""
        response = client.post("/api/v1/tags/some-tag/flush")

        assert response.status_code == 400
        data = response.json()
        assert "confirm_walk_filesystem" in data["detail"].lower()

    def test_confirm_walk_filesystem_false_returns_400(self, client: TestClient) -> None:
        """confirm_walk_filesystem=false returns 400."""
        response = client.post(
            "/api/v1/tags/some-tag/flush",
            params={"confirm_walk_filesystem": False},
        )

        assert response.status_code == 400
        data = response.json()
        assert "confirm_walk_filesystem" in data["detail"].lower()


class TestFlushUnknownTag:
    """Tests for flushing unknown tags."""

    def test_flush_unknown_tag_returns_empty_result(self, client: TestClient) -> None:
        """Flushing a tag that doesn't exist returns empty result."""
        mock_result = {
            "tag": "nonexistent-tag",
            "dry_run": False,
            "artifacts_affected": 0,
            "artifacts": [],
        }

        with patch(
            "magpie.server.routes.tags.run_ctl_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = client.post(
                "/api/v1/tags/nonexistent-tag/flush",
                params={"confirm_walk_filesystem": True},
            )

        assert response.status_code == 200
        data = response.json()

        assert data["tag"] == "nonexistent-tag"
        assert data["artifacts_affected"] == 0
        assert data["artifacts"] == []


class TestResponseFormat:
    """Tests for response format and content."""

    def test_response_has_all_fields(self, client: TestClient) -> None:
        """Response has all expected fields."""
        mock_result = {
            "tag": "any-tag",
            "dry_run": False,
            "artifacts_affected": 0,
            "artifacts": [],
        }

        with patch(
            "magpie.server.routes.tags.run_ctl_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = client.post(
                "/api/v1/tags/any-tag/flush",
                params={"confirm_walk_filesystem": True},
            )

        assert response.status_code == 200
        data = response.json()

        expected_fields = {"tag", "artifacts", "artifacts_affected", "dry_run"}
        assert expected_fields == set(data.keys())

    def test_dry_run_defaults_to_false(self, client: TestClient) -> None:
        """dry_run parameter defaults to false when not specified."""
        mock_result = {
            "tag": "default-test",
            "dry_run": False,
            "artifacts_affected": 0,
            "artifacts": [],
        }

        mock_run = AsyncMock(return_value=mock_result)

        with patch("magpie.server.routes.tags.run_ctl_command", new=mock_run):
            response = client.post(
                "/api/v1/tags/default-test/flush",
                params={"confirm_walk_filesystem": True},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is False

        # Verify --dry-run flag was NOT passed to subprocess
        cmd = mock_run.call_args[0][0]
        assert "--dry-run" not in cmd


class TestFlushSubprocessIntegration:
    """Tests for flush endpoint subprocess error handling."""

    def test_flush_subprocess_error_returns_500(self, client: TestClient) -> None:
        """Flush subprocess failure returns 500 error."""
        from magpie.server.subprocess_utils import CtlCommandError

        with patch(
            "magpie.server.routes.tags.run_ctl_command",
            new=AsyncMock(side_effect=CtlCommandError("Command failed")),
        ):
            response = client.post(
                "/api/v1/tags/test-tag/flush",
                params={"confirm_walk_filesystem": True},
            )

        assert response.status_code == 500
        assert "Command failed" in response.json()["detail"]

    def test_flush_passes_tag_name_to_subprocess(self, client: TestClient) -> None:
        """Flush passes tag name to subprocess command."""
        mock_result = {
            "tag": "my-tag",
            "dry_run": False,
            "artifacts_affected": 0,
            "artifacts": [],
        }

        mock_run = AsyncMock(return_value=mock_result)

        with patch("magpie.server.routes.tags.run_ctl_command", new=mock_run):
            response = client.post(
                "/api/v1/tags/my-tag/flush",
                params={"confirm_walk_filesystem": True},
            )

        assert response.status_code == 200
        # Verify command includes tag name
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "my-tag" in cmd
        assert "flush-tag" in cmd
        assert "--json-output" in cmd
