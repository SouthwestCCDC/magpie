"""FastAPI dependency injection for Magpie server."""

from __future__ import annotations

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


def get_token_service() -> TokenService:
    """Get TokenService instance configured from settings.

    Returns:
        TokenService instance using current application settings.
    """
    settings = get_settings()
    return TokenService(settings)


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


def require_authentication() -> None:
    """Dependency that requires authentication via Caddy forward_auth.

    Checks for the presence of the X-Magpie-Scope header, which is set by
    Caddy's forward_auth middleware after successful token validation. If
    the header is missing, it means the request bypassed authentication
    (e.g., from an IP not in MAGPIE_ALLOWED_CIDRS when forward_auth is
    conditionally applied, or the auth failed).

    This dependency should be used on endpoints that need to ensure
    authentication happens BEFORE any path resolution or business logic,
    preventing information disclosure through different response codes
    (401 vs 404).

    Raises:
        HTTPException 401: If X-Magpie-Scope header is missing (unauthenticated).
    """
    # This function intentionally does NOT take x_magpie_scope as a parameter
    # because we want to enforce that it must be present. FastAPI will
    # handle extracting it via the _validate_scope_header call below.
    # We use a private helper that will raise 401 if the header is missing.


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
