"""CLI utility functions.

This module re-exports error handling functions from the errors module
for backward compatibility. New code should import directly from
magpie.cli.errors instead.
"""

from __future__ import annotations

# Re-export error handling functions for backward compatibility
# New code should import from magpie.cli.errors directly
from magpie.cli.errors import (
    TOKEN_MASK,
    format_auth_error,
    handle_http_error,
    mask_token,
)

__all__ = [
    "TOKEN_MASK",
    "format_auth_error",
    "handle_http_error",
    "mask_token",
]
