"""Integration tests for the flush tag endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class TestDryRunPreview:
    """Tests for dry_run mode returning preview without modification."""

    def test_dry_run_returns_preview(self, client_no_raise: TestClient) -> None:
        """Dry run returns affected artifacts."""
        mock_result = {
            "tag_name": "release",
            "dry_run": True,
            "count": 2,
            "affected_artifacts": ["flush-test/artifact1", "flush-test/artifact2"],
        }

        with patch(
            "magpie.server.routes.tags.run_ctl_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = client_no_raise.post(
                "/api/v1/tags/release/flush",
                params={"confirm_walk_filesystem": True, "dry_run": True},
            )

        assert response.status_code == 200
        data = response.json()

        assert data["tag_name"] == "release"
        assert data["count"] == 2
        assert data["dry_run"] is True
        assert "flush-test/artifact1" in data["affected_artifacts"]
        assert "flush-test/artifact2" in data["affected_artifacts"]


class TestConfirmedFlush:
    """Tests for confirmed flush."""

    def test_confirmed_flush_calls_subprocess(self, client_no_raise: TestClient) -> None:
        """Confirmed flush calls subprocess and returns result."""
        mock_result = {
            "tag_name": "to-flush",
            "dry_run": False,
            "count": 2,
            "affected_artifacts": ["flush-confirm/art1", "flush-confirm/art2"],
        }

        with patch(
            "magpie.server.routes.tags.run_ctl_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = client_no_raise.post(
                "/api/v1/tags/to-flush/flush",
                params={"confirm_walk_filesystem": True, "dry_run": False},
            )

        assert response.status_code == 200
        data = response.json()

        assert data["tag_name"] == "to-flush"
        assert data["count"] == 2
        assert data["dry_run"] is False


class TestMissingConfirmation:
    """Tests for missing confirmation parameter."""

    def test_missing_confirmation_returns_400(self, client_no_raise: TestClient) -> None:
        """Missing confirm_walk_filesystem parameter returns 400."""
        response = client_no_raise.post("/api/v1/tags/some-tag/flush")

        assert response.status_code == 400
        data = response.json()
        assert "confirm_walk_filesystem" in data["detail"].lower()

    def test_confirm_walk_filesystem_false_returns_400(self, client_no_raise: TestClient) -> None:
        """confirm_walk_filesystem=false returns 400."""
        response = client_no_raise.post(
            "/api/v1/tags/some-tag/flush",
            params={"confirm_walk_filesystem": False},
        )

        assert response.status_code == 400
        data = response.json()
        assert "confirm_walk_filesystem" in data["detail"].lower()


class TestFlushUnknownTag:
    """Tests for flushing unknown tags."""

    def test_flush_unknown_tag_returns_empty_result(self, client_no_raise: TestClient) -> None:
        """Flushing a tag that doesn't exist returns empty result."""
        mock_result = {
            "tag_name": "nonexistent-tag",
            "dry_run": False,
            "count": 0,
            "affected_artifacts": [],
        }

        with patch(
            "magpie.server.routes.tags.run_ctl_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = client_no_raise.post(
                "/api/v1/tags/nonexistent-tag/flush",
                params={"confirm_walk_filesystem": True},
            )

        assert response.status_code == 200
        data = response.json()

        assert data["tag_name"] == "nonexistent-tag"
        assert data["count"] == 0
        assert data["affected_artifacts"] == []


class TestResponseFormat:
    """Tests for response format and content."""

    def test_response_has_all_fields(self, client_no_raise: TestClient) -> None:
        """Response has all expected fields."""
        mock_result = {
            "tag_name": "any-tag",
            "dry_run": False,
            "count": 0,
            "affected_artifacts": [],
        }

        with patch(
            "magpie.server.routes.tags.run_ctl_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = client_no_raise.post(
                "/api/v1/tags/any-tag/flush",
                params={"confirm_walk_filesystem": True},
            )

        assert response.status_code == 200
        data = response.json()

        expected_fields = {"tag_name", "affected_artifacts", "count", "dry_run"}
        assert expected_fields == set(data.keys())

    def test_dry_run_defaults_to_false(self, client_no_raise: TestClient) -> None:
        """dry_run parameter defaults to false when not specified."""
        mock_result = {
            "tag_name": "default-test",
            "dry_run": False,
            "count": 0,
            "affected_artifacts": [],
        }

        mock_run = AsyncMock(return_value=mock_result)

        with patch("magpie.server.routes.tags.run_ctl_command", new=mock_run):
            response = client_no_raise.post(
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

    def test_flush_subprocess_error_returns_500(self, client_no_raise: TestClient) -> None:
        """Flush subprocess failure returns 500 error."""
        from magpie.server.subprocess_utils import CtlCommandError

        with patch(
            "magpie.server.routes.tags.run_ctl_command",
            new=AsyncMock(side_effect=CtlCommandError("Command failed")),
        ):
            response = client_no_raise.post(
                "/api/v1/tags/test-tag/flush",
                params={"confirm_walk_filesystem": True},
            )

        assert response.status_code == 500
        # Error message should be sanitized (not expose internal details)
        assert response.json()["detail"] == "Flush tag operation failed"

    def test_flush_passes_tag_name_to_subprocess(self, client_no_raise: TestClient) -> None:
        """Flush passes tag name to subprocess command."""
        mock_result = {
            "tag_name": "my-tag",
            "dry_run": False,
            "count": 0,
            "affected_artifacts": [],
        }

        mock_run = AsyncMock(return_value=mock_result)

        with patch("magpie.server.routes.tags.run_ctl_command", new=mock_run):
            response = client_no_raise.post(
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
