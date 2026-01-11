"""HTTP client helper for Magpie CLI."""

from __future__ import annotations

import httpx

from magpie.cli.config import DEFAULT_TIMEOUT

# Short timeout for connection establishment (30 seconds)
DEFAULT_CONNECT_TIMEOUT = 30.0


def get_client(
    server: str,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
) -> httpx.Client:
    """Create configured httpx client with auth.

    Uses differentiated timeouts: a short timeout for connection establishment
    (suitable for quick operations like `ls`) and a longer timeout for read/write
    operations (suitable for large file transfers).

    Args:
        server: Base URL for the Magpie server.
        token: Optional authentication token.
        timeout: Read/write timeout in seconds. Defaults to 600 (10 minutes).
            Used for potentially long-running operations like file transfers.
        connect_timeout: Connection timeout in seconds. Defaults to 30.
            Used for connection establishment and quick operations.

    Returns:
        Configured httpx.Client instance.
    """
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # Use short connect timeout, long read/write timeout for file transfers
    http_timeout = httpx.Timeout(
        connect=connect_timeout,
        read=timeout,
        write=timeout,
        pool=connect_timeout,
    )
    return httpx.Client(base_url=server, headers=headers, timeout=http_timeout)
