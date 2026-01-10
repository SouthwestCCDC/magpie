"""Authentication module for Magpie - token-based API authentication."""

from magpie.auth.database import (
    delete_token,
    get_connection,
    get_token_by_hash,
    init_database,
    list_tokens,
    save_token,
)
from magpie.auth.models import Token, TokenScope
from magpie.auth.service import TokenInfo, TokenService

__all__ = [
    # Service (main API)
    "TokenService",
    "TokenInfo",
    # Models
    "Token",
    "TokenScope",
    # Database operations
    "init_database",
    "get_connection",
    "save_token",
    "get_token_by_hash",
    "list_tokens",
    "delete_token",
]
