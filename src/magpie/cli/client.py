"""HTTP client helper for Magpie CLI."""

from __future__ import annotations

import httpx


def get_client(server: str, token: str | None = None) -> httpx.Client:
    """Create configured httpx client with auth.

    Args:
        server: Base URL for the Magpie server.
        token: Optional authentication token.

    Returns:
        Configured httpx.Client instance.
    """
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # Long timeout for large file uploads (30 min)
    timeout = httpx.Timeout(30.0, read=1800.0, write=1800.0)
    return httpx.Client(base_url=server, headers=headers, timeout=timeout)
