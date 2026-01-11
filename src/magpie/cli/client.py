"""HTTP client helper for Magpie CLI."""

from __future__ import annotations

import httpx

# Default timeout in seconds (10 minutes) for large file uploads
DEFAULT_TIMEOUT = 600.0


def get_client(
    server: str, token: str | None = None, timeout: float = DEFAULT_TIMEOUT
) -> httpx.Client:
    """Create configured httpx client with auth.

    Args:
        server: Base URL for the Magpie server.
        token: Optional authentication token.
        timeout: Request timeout in seconds. Defaults to 600 (10 minutes).

    Returns:
        Configured httpx.Client instance.
    """
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # Use same timeout for connect, read, and write operations
    http_timeout = httpx.Timeout(timeout, read=timeout, write=timeout)
    return httpx.Client(base_url=server, headers=headers, timeout=http_timeout)
