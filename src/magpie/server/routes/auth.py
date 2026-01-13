"""Auth endpoints for token validation and management."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

from magpie.auth.database import get_connection, list_tokens
from magpie.auth.models import TokenScope
from magpie.auth.service import TokenInfo, TokenService
from magpie.server.deps import get_token_service, require_admin_scope

router = APIRouter()

# Token name validation pattern: alphanumeric start, then alphanumeric, dots, underscores, hyphens
# Maximum 64 characters total (1 required start + up to 63 more)
TOKEN_NAME_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$"  # nosec B105 - not a password, just a token name regex

# Request/Response models for token management


class CreateTokenRequest(BaseModel):
    """Request model for creating a new token."""

    name: str = Field(pattern=TOKEN_NAME_PATTERN, max_length=64)
    scope: str  # "read", "write", or "admin"


class CreateTokenResponse(BaseModel):
    """Response model for token creation.

    NOTE: The token field contains the plaintext token.
    This is the ONLY time the plaintext token will be visible!
    """

    name: str
    token: str  # Plaintext - only visible once!
    scope: str


class TokenListItem(BaseModel):
    """Single token item in list response.

    NOTE: Never includes token_hash for security.
    """

    name: str
    scope: str
    enabled: bool
    created_at: datetime


class TokenListResponse(BaseModel):
    """Response model for listing tokens."""

    tokens: list[TokenListItem]


@router.get("/api/v1/auth/validate")
async def validate_auth(
    authorization: Annotated[str | None, Header()] = None,
    token_service: Annotated[TokenService, Depends(get_token_service)] = None,
) -> Response:
    """Validate a token for Caddy forward_auth integration.

    Extracts the Bearer token from the Authorization header and validates it.
    On success, returns 200 with identity headers that Caddy can forward
    to upstream services.

    Args:
        authorization: Authorization header value (Bearer <token>).

    Returns:
        200 OK with X-Magpie-User and X-Magpie-Scope headers on success.

    Raises:
        HTTPException 401: If token is missing, malformed, invalid, or disabled.
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

    # Return success with identity headers
    return Response(
        status_code=status.HTTP_200_OK,
        headers={
            "X-Magpie-User": token_info.name,
            "X-Magpie-Scope": token_info.scope.value,
        },
    )


# Token management endpoints (admin only)


@router.post("/api/v1/tokens")
async def create_token(
    request: CreateTokenRequest,
    admin_info: Annotated[TokenInfo, Depends(require_admin_scope)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
) -> CreateTokenResponse:
    """Create a new authentication token.

    Requires admin scope. The plaintext token is only returned once in this
    response and cannot be retrieved later.

    Args:
        request: Token creation request with name and scope.
        admin_info: Validated admin token info (from dependency).
        token_service: TokenService instance.

    Returns:
        CreateTokenResponse with the plaintext token (only visible once!).

    Raises:
        HTTPException 400: If scope is invalid or token name already exists.
        HTTPException 401: If Authorization header is missing or invalid.
        HTTPException 403: If token doesn't have admin scope.
    """
    # Validate scope
    try:
        scope = TokenScope(request.scope)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid scope: {request.scope}. Must be 'read', 'write', or 'admin'",
        )

    # Create token
    try:
        plaintext_token = token_service.create_token(request.name, scope)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    return CreateTokenResponse(
        name=request.name,
        token=plaintext_token,
        scope=scope.value,
    )


@router.get("/api/v1/tokens")
async def list_all_tokens(
    admin_info: Annotated[TokenInfo, Depends(require_admin_scope)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
) -> TokenListResponse:
    """List all authentication tokens.

    Requires admin scope. Returns token metadata but never exposes token hashes.

    Args:
        admin_info: Validated admin token info (from dependency).
        token_service: TokenService instance.

    Returns:
        TokenListResponse with all tokens (no hashes exposed).

    Raises:
        HTTPException 401: If Authorization header is missing or invalid.
        HTTPException 403: If token doesn't have admin scope.
    """
    conn = get_connection(token_service.db_path)
    try:
        tokens = list_tokens(conn)
    finally:
        conn.close()

    token_items = [
        TokenListItem(
            name=token.name,
            scope=token.scope.value,
            enabled=token.enabled,
            created_at=token.created_at,
        )
        for token in tokens
    ]

    return TokenListResponse(tokens=token_items)


@router.delete("/api/v1/tokens/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    name: str,
    admin_info: Annotated[TokenInfo, Depends(require_admin_scope)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
) -> Response:
    """Revoke (delete) an authentication token.

    Requires admin scope. Permanently removes the token from the database.

    Args:
        name: Name of the token to revoke.
        admin_info: Validated admin token info (from dependency).
        token_service: TokenService instance.

    Returns:
        204 No Content on success.

    Raises:
        HTTPException 401: If Authorization header is missing or invalid.
        HTTPException 403: If token doesn't have admin scope.
        HTTPException 404: If token with given name doesn't exist.
    """
    revoked = token_service.revoke_token(name)

    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Token '{name}' not found",
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
