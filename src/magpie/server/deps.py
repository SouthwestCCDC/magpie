"""FastAPI dependency injection for Magpie server."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from magpie.auth.models import TokenScope
from magpie.auth.service import TokenInfo, TokenService
from magpie.config import get_settings
from magpie.storage.service import StorageService


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
