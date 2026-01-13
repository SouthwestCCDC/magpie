"""Shared validation patterns and functions for name validation.

This module provides centralized validation for token names and tag names,
ensuring consistent enforcement regardless of entry point (API or CLI).
"""

from __future__ import annotations

import re

# Token name validation: alphanumeric start, then alphanumeric, dots, underscores, hyphens
# Maximum 64 characters
TOKEN_NAME_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$"
TOKEN_NAME_MAX_LENGTH = 64
_TOKEN_NAME_RE = re.compile(TOKEN_NAME_PATTERN)

# Tag name validation: same pattern as tokens, but max 128 characters
TAG_NAME_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$"
TAG_NAME_MAX_LENGTH = 128
_TAG_NAME_RE = re.compile(TAG_NAME_PATTERN)


class ValidationError(ValueError):
    """Raised when validation fails for names or other inputs."""

    pass


def validate_token_name(name: str) -> str:
    """Validate a token name.

    Token names must:
    - Start with an alphanumeric character
    - Contain only alphanumeric characters, dots (.), underscores (_), or hyphens (-)
    - Be at most 64 characters long

    Args:
        name: The token name to validate.

    Returns:
        The validated name (unchanged if valid).

    Raises:
        ValidationError: If the name is invalid.
    """
    if not name:
        raise ValidationError("Token name cannot be empty")

    if len(name) > TOKEN_NAME_MAX_LENGTH:
        raise ValidationError(
            f"Token name exceeds maximum length of {TOKEN_NAME_MAX_LENGTH} characters"
        )

    if not _TOKEN_NAME_RE.match(name):
        raise ValidationError(
            "Token name must start with alphanumeric and contain only "
            "alphanumeric, dots, underscores, or hyphens"
        )

    return name


def validate_tag_name(name: str) -> str:
    """Validate a tag name.

    Tag names must:
    - Start with an alphanumeric character
    - Contain only alphanumeric characters, dots (.), underscores (_), or hyphens (-)
    - Be at most 128 characters long

    Args:
        name: The tag name to validate.

    Returns:
        The validated name (unchanged if valid).

    Raises:
        ValidationError: If the name is invalid.
    """
    if not name:
        raise ValidationError("Tag name cannot be empty")

    if len(name) > TAG_NAME_MAX_LENGTH:
        raise ValidationError(
            f"Tag name exceeds maximum length of {TAG_NAME_MAX_LENGTH} characters"
        )

    if not _TAG_NAME_RE.match(name):
        raise ValidationError(
            "Tag name must start with alphanumeric and contain only "
            "alphanumeric, dots, underscores, or hyphens"
        )

    return name
