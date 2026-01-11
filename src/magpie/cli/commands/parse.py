"""Artifact reference parsing utilities for CLI commands.

This module provides consistent parsing of artifact references across all CLI
commands. The canonical format uses colon (:) as the separator between path
and ref:

    path:ref

Examples:
    images/ubuntu:latest       -> (images/ubuntu, latest)
    images/ubuntu:@abc12345    -> (images/ubuntu, @abc12345)
    images/ubuntu              -> (images/ubuntu, latest)  [default ref]

The @ prefix is used for hash references (short hashes), while bare names
are tag references.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Pattern for validating hash refs: @ followed by 8 hex characters
HASH_REF_PATTERN = re.compile(r"^@[0-9a-f]{8}$")

# Pattern for valid tag names: alphanumeric, dash, underscore, dot
# Must start with alphanumeric
TAG_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")

# Pattern for valid path components: same as tags
PATH_COMPONENT_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


class ParseError(Exception):
    """Error parsing artifact reference."""

    pass


@dataclass
class ArtifactRef:
    """Parsed artifact reference.

    Attributes:
        path: Artifact path (e.g., "images/ubuntu").
        ref: Reference to a specific version - either a tag name (e.g., "latest")
             or a hash ref (e.g., "@abc12345").
    """

    path: str
    ref: str

    @property
    def is_hash_ref(self) -> bool:
        """Check if ref is a hash reference (starts with @)."""
        return self.ref.startswith("@")


def parse_artifact_ref(artifact_ref: str, default_ref: str = "latest") -> tuple[str, str]:
    """Parse artifact reference into path and ref.

    Format: path:ref or path (defaults to provided default_ref)

    Uses colon (:) as the separator between path and ref. This is the
    canonical format that should be used consistently across all CLI commands.

    Examples:
        "images/ubuntu:latest" -> ("images/ubuntu", "latest")
        "images/ubuntu:@abc12345" -> ("images/ubuntu", "@abc12345")
        "images/ubuntu" -> ("images/ubuntu", "latest")
        "project/images/ubuntu:v1.0" -> ("project/images/ubuntu", "v1.0")

    Args:
        artifact_ref: Artifact reference string in path:ref format.
        default_ref: Default ref if none specified (default: "latest").

    Returns:
        Tuple of (path, ref).

    Raises:
        ParseError: If the reference format is invalid.
    """
    if not artifact_ref:
        raise ParseError("Artifact reference cannot be empty")

    # Check for common mistakes
    _validate_no_double_colon(artifact_ref)

    if ":" in artifact_ref:
        # Split on last colon to support paths with colons (edge case)
        idx = artifact_ref.rfind(":")
        path = artifact_ref[:idx]
        ref = artifact_ref[idx + 1 :]

        if not path:
            raise ParseError(f"Empty path in reference: {artifact_ref}")
        if not ref:
            raise ParseError(f"Empty ref after colon in reference: {artifact_ref}")
    else:
        path = artifact_ref
        ref = default_ref

    # Validate path
    _validate_path(path)

    # Validate ref
    _validate_ref(ref)

    return path, ref


def parse_artifact_path(artifact_input: str) -> str:
    """Parse artifact path, stripping any ref if accidentally provided.

    This is for commands that only accept a path (like `ls`). If the user
    provides a ref (path:ref format), the ref is stripped and only the
    path is returned. Leading slashes are also normalized away.

    Examples:
        "images/ubuntu" -> "images/ubuntu"
        "images/ubuntu:latest" -> "images/ubuntu" (ref stripped)
        "/images/ubuntu" -> "images/ubuntu" (leading slash stripped)

    Args:
        artifact_input: Artifact path or path:ref string.

    Returns:
        Just the artifact path (normalized).

    Raises:
        ParseError: If the path format is invalid.
    """
    if not artifact_input:
        raise ParseError("Artifact path cannot be empty")

    # Normalize: strip leading slashes
    path = artifact_input.lstrip("/")

    # If there's a colon, extract just the path
    if ":" in path:
        idx = path.rfind(":")
        path = path[:idx]
        # Note: We silently accept and strip the ref for UX

    if not path:
        raise ParseError(f"Empty path in input: {artifact_input}")

    # Validate path
    _validate_path(path)

    return path


def _validate_no_double_colon(artifact_ref: str) -> None:
    """Check for double colon which indicates a parsing error.

    This catches cases like "path/artifact:latest:latest" which could happen
    if a ref is accidentally appended twice.
    """
    if artifact_ref.count(":") > 1:
        raise ParseError(
            f"Multiple colons in reference '{artifact_ref}'. "
            f"Expected format: path:ref (e.g., 'images/ubuntu:latest')"
        )


def _validate_path(path: str) -> None:
    """Validate artifact path format.

    Path components must be alphanumeric (with dash, underscore, dot allowed).
    Each component must start with an alphanumeric character.
    """
    if not path:
        raise ParseError("Path cannot be empty")

    # Split path into components
    components = path.split("/")

    for component in components:
        if not component:
            raise ParseError(f"Empty component in path '{path}' (double slash?)")

        if not PATH_COMPONENT_PATTERN.match(component):
            raise ParseError(
                f"Invalid path component '{component}' in '{path}'. "
                f"Components must start with alphanumeric and contain only "
                f"alphanumeric, dash, underscore, or dot characters."
            )


def _validate_ref(ref: str) -> None:
    """Validate ref format (tag name or hash ref).

    Hash refs start with @ followed by 8 hex characters.
    Tag names are alphanumeric with dash, underscore, dot allowed.
    """
    if not ref:
        raise ParseError("Ref cannot be empty")

    if ref.startswith("@"):
        # Hash ref - must be @ followed by 8 hex chars
        if not HASH_REF_PATTERN.match(ref):
            # Provide helpful error for common mistakes
            if len(ref) < 9:
                raise ParseError(
                    f"Invalid hash ref '{ref}'. Hash refs must be @ followed by "
                    f"exactly 8 hex characters (e.g., '@abc12345')."
                )
            elif len(ref) > 9:
                raise ParseError(
                    f"Hash ref '{ref}' is too long. Use the short form with "
                    f"8 hex characters (e.g., '@{ref[1:9]}')."
                )
            else:
                raise ParseError(
                    f"Invalid hash ref '{ref}'. Must contain only hex characters "
                    f"(0-9, a-f) after the @ symbol."
                )
    else:
        # Tag name
        if not TAG_PATTERN.match(ref):
            raise ParseError(
                f"Invalid tag name '{ref}'. Tags must start with alphanumeric "
                f"and contain only alphanumeric, dash, underscore, or dot characters."
            )
