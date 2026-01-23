"""HTTP client helper for Magpie CLI."""

from __future__ import annotations

import ssl
from pathlib import Path

import httpx
import truststore

from magpie.cli.config import DEFAULT_TIMEOUT

# Short timeout for connection establishment (30 seconds)
DEFAULT_CONNECT_TIMEOUT = 30.0


def _create_ssl_context(ca_cert_path: str | None = None) -> ssl.SSLContext:
    """Create SSL context using system trust store with optional custom CA.

    Args:
        ca_cert_path: Optional path to additional CA certificate file.

    Returns:
        Configured SSL context.
    """
    # Create SSL context with system trust store
    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    # Load additional CA certificate if provided
    if ca_cert_path:
        ca_path = Path(ca_cert_path)
        if not ca_path.exists():
            raise FileNotFoundError(f"CA certificate not found: {ca_cert_path}")
        ctx.load_verify_locations(cafile=str(ca_path))

    return ctx


def get_client(
    server: str,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    ca_cert: str | None = None,
) -> httpx.Client:
    """Create configured httpx client with auth.

    Uses differentiated timeouts: a short timeout for connection establishment
    (suitable for quick operations like `ls`) and a longer timeout for read/write
    operations (suitable for large file transfers).

    Uses system trust store by default for SSL certificate validation, with
    optional support for additional CA certificates.

    Args:
        server: Base URL for the Magpie server.
        token: Optional authentication token.
        timeout: Read/write timeout in seconds. Defaults to 600 (10 minutes).
            Used for potentially long-running operations like file transfers.
        connect_timeout: Connection timeout in seconds. Defaults to 30.
            Used for connection establishment and quick operations.
        ca_cert: Optional path to additional CA certificate file for SSL verification.

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

    # Create SSL context with system trust store
    ssl_context = _create_ssl_context(ca_cert_path=ca_cert)

    return httpx.Client(base_url=server, headers=headers, timeout=http_timeout, verify=ssl_context)
