"""FastAPI dependency injection for Magpie server."""

from __future__ import annotations

import functools
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from magpie.auth.models import TokenScope
from magpie.auth.service import TokenInfo, TokenService
from magpie.config import MagpieSettings, get_settings
from magpie.storage.service import StorageService


def get_magpie_settings() -> MagpieSettings:
    """Get MagpieSettings instance (cached singleton).

    Returns:
        MagpieSettings instance using current application settings.
    """
    return get_settings()


def get_storage_service() -> StorageService:
    """Get StorageService instance configured from settings.

    Returns:
        StorageService instance using current application settings.
    """
    settings = get_settings()
    return StorageService(settings)


@functools.lru_cache(maxsize=1)
def get_token_service() -> TokenService:
    """Get TokenService instance configured from settings (cached singleton).

    Performance Optimization (Issue #426):
        The TokenService is cached using @lru_cache(maxsize=1) to avoid recreating
        it on every request. This provides at least an order-of-magnitude performance
        improvement in typical environments. See benchmark tests in
        tests/unit/test_token_service_performance.py for empirical measurements;
        exact ratios will vary by machine, OS, and filesystem.

        Without caching, TokenService.__init__() would call init_database() on every
        request. While init_database() is idempotent (uses CREATE TABLE IF NOT EXISTS),
        this incurs significant overhead (connection setup, WAL mode pragma, schema check).

    Thread Safety:
        - lru_cache is thread-safe (uses internal locking)
        - TokenService instances are immutable after construction
        - SQLite connections are created per-operation, not shared
        - WAL mode provides concurrent read access

    Configuration Changes:
        Changes to MagpieSettings after the first call will not be reflected in the
        TokenService. In production, settings should be configured once at application
        startup. For testing scenarios where settings change, use
        clear_token_service_cache() to invalidate the cache before creating a new
        TokenService with different settings.

    Returns:
        Cached TokenService instance using current application settings.
    """
    settings = get_settings()
    return TokenService(settings)


def clear_token_service_cache() -> None:
    """Clear the cached TokenService instance.

    This is primarily useful in testing scenarios where settings change
    and a fresh TokenService instance is needed. In production, settings
    should be configured once at application startup, so this should not
    be necessary.

    Note: This only clears the TokenService cache. If you've changed environment
    variables and need to pick up new settings, you'll also need to clear the
    get_settings() cache separately using get_settings.cache_clear().

    Example:
        >>> clear_token_service_cache()
        >>> service = get_token_service()  # Creates new instance with current settings
    """
    get_token_service.cache_clear()


def _validate_scope_header(x_magpie_scope: str | None) -> str:
    """Validate and return the scope header value.

    Shared helper function for scope validation that checks if the
    X-Magpie-Scope header is present and contains a valid scope value.

    Args:
        x_magpie_scope: Scope header value from Caddy forward_auth.

    Returns:
        The validated scope value.

    Raises:
        HTTPException 401: If X-Magpie-Scope header is missing (unauthenticated).
        HTTPException 403: If scope value is not a valid TokenScope enum value.
    """
    if x_magpie_scope is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    # Validate scope value is one of the allowed scopes
    valid_scopes = {scope.value for scope in TokenScope}
    if x_magpie_scope not in valid_scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Invalid scope: {x_magpie_scope}",
        )

    return x_magpie_scope


def require_write_scope(
    x_magpie_scope: Annotated[str | None, Header(alias="X-Magpie-Scope")] = None,
) -> None:
    """Dependency that requires write or admin scope from Caddy forward_auth.

    Checks the X-Magpie-Scope header set by Caddy's forward_auth middleware
    and ensures the token has write or admin scope. Read-only tokens are rejected.

    Args:
        x_magpie_scope: Scope header value from Caddy forward_auth.

    Raises:
        HTTPException 401: If X-Magpie-Scope header is missing (unauthenticated).
        HTTPException 403: If token has invalid or read scope (insufficient permissions).
    """
    validated_scope = _validate_scope_header(x_magpie_scope)

    if validated_scope == TokenScope.READ.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Write or admin scope required",
        )


def require_admin_scope_header(
    x_magpie_scope: Annotated[str | None, Header(alias="X-Magpie-Scope")] = None,
) -> None:
    """Dependency that requires admin scope from Caddy forward_auth.

    Checks the X-Magpie-Scope header set by Caddy's forward_auth middleware
    and ensures the token has admin scope.

    Args:
        x_magpie_scope: Scope header value from Caddy forward_auth.

    Raises:
        HTTPException 401: If X-Magpie-Scope header is missing (unauthenticated).
        HTTPException 403: If token has invalid or non-admin scope (insufficient permissions).
    """
    validated_scope = _validate_scope_header(x_magpie_scope)

    if validated_scope != TokenScope.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin scope required",
        )


def require_read_scope(
    x_magpie_scope: Annotated[str | None, Header(alias="X-Magpie-Scope")] = None,
) -> None:
    """Dependency that requires read, write, or admin scope from Caddy forward_auth.

    Checks the X-Magpie-Scope header set by Caddy's forward_auth middleware
    and ensures the token has at least read scope. This enforces authentication
    BEFORE any path resolution or business logic.

    Args:
        x_magpie_scope: Scope header value from Caddy forward_auth.

    Raises:
        HTTPException 401: If X-Magpie-Scope header is missing (unauthenticated).
        HTTPException 403: If scope value is not a valid TokenScope enum value.
    """
    # Validate scope header (raises 401 if missing, 403 if invalid)
    _validate_scope_header(x_magpie_scope)


def require_admin_scope(
    authorization: Annotated[str | None, Header()] = None,
    token_service: Annotated[TokenService, Depends(get_token_service)] = None,
) -> TokenInfo:
    """Dependency that validates token and requires admin scope.

    Extracts the Bearer token from the Authorization header, validates it,
    and ensures the token has admin scope.

    Args:
        authorization: Authorization header value (Bearer <token>).
        token_service: TokenService instance for validation.

    Returns:
        TokenInfo for the validated admin token.

    Raises:
        HTTPException 401: If token is missing, malformed, invalid, or disabled.
        HTTPException 403: If token doesn't have admin scope.
    """
    # Check for missing Authorization header
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    # Check for Bearer prefix
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed Authorization header: expected 'Bearer <token>'",
        )

    # Extract token
    token = authorization[7:]  # Remove "Bearer " prefix

    # Validate token
    token_info = token_service.validate_token(token)

    if token_info is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or disabled token",
        )

    # Check for admin scope
    if token_info.scope != TokenScope.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin scope required",
        )

    return token_info
