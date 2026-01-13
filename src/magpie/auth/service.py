"""Token service for authentication operations."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from magpie.auth.database import (
    delete_token,
    get_connection,
    get_token_by_hash,
    init_database,
    save_token,
)
from magpie.auth.models import Token, TokenScope
from magpie.validation import validate_token_name

if TYPE_CHECKING:
    from magpie.config import MagpieSettings


# Scope hierarchy levels (higher number = more permissions)
_SCOPE_LEVELS = {
    TokenScope.READ: 1,
    TokenScope.WRITE: 2,
    TokenScope.ADMIN: 3,
}


@dataclass
class TokenInfo:
    """Validation result containing token identity and scope.

    Returned by validate_token when a token is valid and enabled.
    Does not contain sensitive information like the token hash.
    """

    name: str
    scope: TokenScope


class TokenService:
    """Service for token management operations.

    Provides create, validate, and revoke operations for authentication tokens.
    Handles secure token generation, hashing, and scope validation.

    Token Prefix Convention:
        - Regular tokens (read/write): mgp_ prefix
        - Admin tokens: mgp_ADMIN_ prefix
    """

    def __init__(self, config: MagpieSettings) -> None:
        """Initialize the token service.

        Args:
            config: MagpieSettings instance with database_path configuration.
        """
        self.config = config
        self.db_path = config.database_path
        # Ensure database is initialized
        init_database(self.db_path)

    def create_token(self, name: str, scope: TokenScope) -> str:
        """Create a new token and return the plaintext (only visible once).

        Uses secrets.token_urlsafe(32) for secure random generation.
        Stores SHA-256 hash of token, never the plaintext.

        Token Prefix Convention:
            - Regular tokens (read/write): mgp_ prefix
            - Admin tokens: mgp_ADMIN_ prefix

        Args:
            name: Human-readable name for the token (must be unique).
            scope: Permission level (read/write/admin).

        Returns:
            Plaintext token string (mgp_... format) - only returned once.

        Raises:
            ValueError: If token name already exists or is invalid.
            ValidationError: If token name fails validation (invalid format/length).
        """
        # Validate token name (defense in depth - also validated at API layer)
        validate_token_name(name)

        # Generate secure random token
        random_part = secrets.token_urlsafe(32)

        # Add prefix based on scope
        if scope == TokenScope.ADMIN:
            plaintext_token = f"mgp_ADMIN_{random_part}"
        else:
            plaintext_token = f"mgp_{random_part}"

        # Hash the token for storage
        token_hash = self._hash_token(plaintext_token)

        # Create token model
        token = Token(
            name=name,
            token_hash=token_hash,
            scope=scope,
            enabled=True,
            created_at=datetime.now(timezone.utc),
        )

        # Save to database
        conn = get_connection(self.db_path)
        try:
            save_token(conn, token)
        except sqlite3.IntegrityError as e:
            raise ValueError(f"Token name '{name}' already exists") from e
        finally:
            conn.close()

        # Return plaintext (only time it's visible)
        return plaintext_token

    def validate_token(self, token: str) -> TokenInfo | None:
        """Validate a token and return its info if valid.

        Uses timing-safe comparison via hmac.compare_digest() as defense
        in depth against timing attacks.

        Args:
            token: Plaintext token to validate.

        Returns:
            TokenInfo if valid and enabled, None otherwise.
        """
        # Hash the input token
        token_hash = self._hash_token(token)

        # Lookup by hash
        conn = get_connection(self.db_path)
        try:
            stored_token = get_token_by_hash(conn, token_hash)
        finally:
            conn.close()

        # Check if token exists
        if stored_token is None:
            return None

        # Use timing-safe comparison (defense in depth)
        if not hmac.compare_digest(stored_token.token_hash, token_hash):
            return None

        # Check if token is enabled
        if not stored_token.enabled:
            return None

        return TokenInfo(
            name=stored_token.name,
            scope=stored_token.scope,
        )

    def revoke_token(self, name: str) -> bool:
        """Revoke a token by name.

        Permanently deletes the token from the database.

        Args:
            name: Name of the token to revoke.

        Returns:
            True if token was revoked, False if not found.
        """
        conn = get_connection(self.db_path)
        try:
            return delete_token(conn, name)
        finally:
            conn.close()

    def has_scope(self, token_scope: TokenScope, required_scope: TokenScope) -> bool:
        """Check if token_scope satisfies required_scope.

        Hierarchy: admin > write > read
        - ADMIN can access everything
        - WRITE can access write and read operations
        - READ can only access read operations

        Args:
            token_scope: The scope of the token being checked.
            required_scope: The minimum scope required for the operation.

        Returns:
            True if token_scope has sufficient permissions.
        """
        return _SCOPE_LEVELS[token_scope] >= _SCOPE_LEVELS[required_scope]

    def _hash_token(self, token: str) -> str:
        """Hash a token using SHA-256.

        Args:
            token: Plaintext token to hash.

        Returns:
            Hexadecimal SHA-256 hash of the token.
        """
        return hashlib.sha256(token.encode()).hexdigest()
