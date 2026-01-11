"""Unit tests for scope validation dependency functions."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from magpie.server.deps import (
    _validate_scope_header,
    require_admin_scope_header,
    require_write_scope,
)


class TestValidateScopeHeader:
    """Tests for the _validate_scope_header helper function."""

    def test_returns_valid_read_scope(self) -> None:
        """Returns 'read' for valid read scope."""
        result = _validate_scope_header("read")
        assert result == "read"

    def test_returns_valid_write_scope(self) -> None:
        """Returns 'write' for valid write scope."""
        result = _validate_scope_header("write")
        assert result == "write"

    def test_returns_valid_admin_scope(self) -> None:
        """Returns 'admin' for valid admin scope."""
        result = _validate_scope_header("admin")
        assert result == "admin"

    def test_raises_401_for_none_header(self) -> None:
        """Raises 401 when scope header is None (unauthenticated)."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_scope_header(None)

        assert exc_info.value.status_code == 401
        assert "Authentication required" in exc_info.value.detail

    def test_raises_403_for_invalid_scope(self) -> None:
        """Raises 403 for scope values not in TokenScope enum."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_scope_header("superadmin")

        assert exc_info.value.status_code == 403
        assert "Invalid scope" in exc_info.value.detail

    def test_raises_403_for_empty_string(self) -> None:
        """Raises 403 for empty string scope."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_scope_header("")

        assert exc_info.value.status_code == 403
        assert "Invalid scope" in exc_info.value.detail

    def test_raises_403_for_uppercase_scope(self) -> None:
        """Raises 403 for uppercase scope (case-sensitive)."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_scope_header("ADMIN")

        assert exc_info.value.status_code == 403
        assert "Invalid scope" in exc_info.value.detail

    def test_raises_403_for_arbitrary_string(self) -> None:
        """Raises 403 for arbitrary invalid scope string."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_scope_header("invalid")

        assert exc_info.value.status_code == 403
        assert "Invalid scope" in exc_info.value.detail


class TestRequireWriteScope:
    """Tests for the require_write_scope dependency function."""

    def test_allows_write_scope(self) -> None:
        """Write scope passes without raising."""
        # Should not raise
        require_write_scope("write")

    def test_allows_admin_scope(self) -> None:
        """Admin scope passes without raising (admin >= write)."""
        # Should not raise
        require_write_scope("admin")

    def test_rejects_read_scope_with_403(self) -> None:
        """Read scope raises 403 (insufficient permissions)."""
        with pytest.raises(HTTPException) as exc_info:
            require_write_scope("read")

        assert exc_info.value.status_code == 403
        assert "Write or admin scope required" in exc_info.value.detail

    def test_raises_401_for_none_header(self) -> None:
        """Missing header raises 401 (unauthenticated)."""
        with pytest.raises(HTTPException) as exc_info:
            require_write_scope(None)

        assert exc_info.value.status_code == 401
        assert "Authentication required" in exc_info.value.detail

    def test_raises_403_for_invalid_scope(self) -> None:
        """Invalid scope raises 403."""
        with pytest.raises(HTTPException) as exc_info:
            require_write_scope("superadmin")

        assert exc_info.value.status_code == 403
        assert "Invalid scope" in exc_info.value.detail


class TestRequireAdminScopeHeader:
    """Tests for the require_admin_scope_header dependency function."""

    def test_allows_admin_scope(self) -> None:
        """Admin scope passes without raising."""
        # Should not raise
        require_admin_scope_header("admin")

    def test_rejects_write_scope_with_403(self) -> None:
        """Write scope raises 403 (admin required)."""
        with pytest.raises(HTTPException) as exc_info:
            require_admin_scope_header("write")

        assert exc_info.value.status_code == 403
        assert "Admin scope required" in exc_info.value.detail

    def test_rejects_read_scope_with_403(self) -> None:
        """Read scope raises 403 (admin required)."""
        with pytest.raises(HTTPException) as exc_info:
            require_admin_scope_header("read")

        assert exc_info.value.status_code == 403
        assert "Admin scope required" in exc_info.value.detail

    def test_raises_401_for_none_header(self) -> None:
        """Missing header raises 401 (unauthenticated)."""
        with pytest.raises(HTTPException) as exc_info:
            require_admin_scope_header(None)

        assert exc_info.value.status_code == 401
        assert "Authentication required" in exc_info.value.detail

    def test_raises_403_for_invalid_scope(self) -> None:
        """Invalid scope raises 403."""
        with pytest.raises(HTTPException) as exc_info:
            require_admin_scope_header("superadmin")

        assert exc_info.value.status_code == 403
        assert "Invalid scope" in exc_info.value.detail
