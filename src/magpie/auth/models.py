"""Token models for Magpie authentication."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class TokenScope(str, Enum):
    """Permission scope for authentication tokens.

    Defines the level of access granted to a token:
    - READ: Can download artifacts and list/query metadata
    - WRITE: READ permissions plus can upload artifacts and manage tags
    - ADMIN: Full access including token management and flush operations
    """

    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


class Token(BaseModel):
    """Authentication token model.

    Tokens are used to authenticate API requests. The actual token value
    is never stored - only a SHA-256 hash of the token is persisted.

    Token Prefix Convention:
        - Regular tokens: mgp_ prefix (e.g., mgp_abc123...)
        - Break-glass admin token: mgp_ADMIN_ prefix (e.g., mgp_ADMIN_xyz789...)

    Attributes:
        name: Human-readable identifier for the token (unique).
        token_hash: SHA-256 hash of the token value (never store plaintext).
        scope: Permission level granted to this token.
        enabled: Whether the token is active. Can be disabled without deletion.
        created_at: Timestamp when the token was created.
    """

    name: str
    token_hash: str
    scope: TokenScope
    enabled: bool = True
    created_at: datetime
