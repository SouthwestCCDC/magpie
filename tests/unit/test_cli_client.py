"""Tests for CLI client module."""

from __future__ import annotations

import httpx

from magpie.cli.client import (
    DEFAULT_CONNECT_TIMEOUT,
    get_client,
)
from magpie.cli.config import DEFAULT_TIMEOUT


class TestGetClient:
    """Tests for get_client function."""

    def test_returns_httpx_client(self) -> None:
        """Test that get_client returns an httpx.Client instance."""
        client = get_client("http://localhost:8000")
        try:
            assert isinstance(client, httpx.Client)
        finally:
            client.close()

    def test_sets_base_url(self) -> None:
        """Test that base_url is set correctly."""
        client = get_client("http://localhost:8000")
        try:
            assert str(client.base_url) == "http://localhost:8000"
        finally:
            client.close()

    def test_sets_authorization_header_when_token_provided(self) -> None:
        """Test that Authorization header is set when token is provided."""
        client = get_client("http://localhost:8000", token="mgp_testtoken")
        try:
            assert client.headers.get("Authorization") == "Bearer mgp_testtoken"
        finally:
            client.close()

    def test_no_authorization_header_when_no_token(self) -> None:
        """Test that no Authorization header is set when token is None."""
        client = get_client("http://localhost:8000", token=None)
        try:
            assert "Authorization" not in client.headers
        finally:
            client.close()

    def test_no_authorization_header_when_empty_token(self) -> None:
        """Test that no Authorization header is set when token is empty."""
        client = get_client("http://localhost:8000", token="")
        try:
            assert "Authorization" not in client.headers
        finally:
            client.close()


class TestGetClientTimeout:
    """Tests for timeout configuration in get_client."""

    def test_default_timeouts_applied(self) -> None:
        """Test that default timeouts are correctly applied."""
        client = get_client("http://localhost:8000")
        try:
            timeout = client.timeout
            assert timeout.connect == DEFAULT_CONNECT_TIMEOUT
            assert timeout.connect == 30.0
            assert timeout.read == DEFAULT_TIMEOUT
            assert timeout.read == 600.0
            assert timeout.write == DEFAULT_TIMEOUT
            assert timeout.write == 600.0
            assert timeout.pool == DEFAULT_CONNECT_TIMEOUT
        finally:
            client.close()

    def test_custom_read_write_timeout(self) -> None:
        """Test that custom timeout parameter affects read/write timeouts."""
        client = get_client("http://localhost:8000", timeout=120.0)
        try:
            timeout = client.timeout
            # connect timeout should still be default
            assert timeout.connect == DEFAULT_CONNECT_TIMEOUT
            # read/write should use custom value
            assert timeout.read == 120.0
            assert timeout.write == 120.0
        finally:
            client.close()

    def test_custom_connect_timeout(self) -> None:
        """Test that custom connect_timeout parameter is applied."""
        client = get_client("http://localhost:8000", connect_timeout=10.0)
        try:
            timeout = client.timeout
            assert timeout.connect == 10.0
            assert timeout.pool == 10.0
            # read/write should still be default
            assert timeout.read == DEFAULT_TIMEOUT
            assert timeout.write == DEFAULT_TIMEOUT
        finally:
            client.close()

    def test_both_custom_timeouts(self) -> None:
        """Test that both timeout parameters can be customized."""
        client = get_client("http://localhost:8000", timeout=300.0, connect_timeout=15.0)
        try:
            timeout = client.timeout
            assert timeout.connect == 15.0
            assert timeout.pool == 15.0
            assert timeout.read == 300.0
            assert timeout.write == 300.0
        finally:
            client.close()

    def test_zero_timeout_disables_timeout(self) -> None:
        """Test that zero timeout value works (disables timeout)."""
        client = get_client("http://localhost:8000", timeout=0.0, connect_timeout=0.0)
        try:
            timeout = client.timeout
            assert timeout.connect == 0.0
            assert timeout.read == 0.0
            assert timeout.write == 0.0
            assert timeout.pool == 0.0
        finally:
            client.close()

    def test_timeout_object_type(self) -> None:
        """Test that timeout is an httpx.Timeout object."""
        client = get_client("http://localhost:8000")
        try:
            assert isinstance(client.timeout, httpx.Timeout)
        finally:
            client.close()

    def test_different_connect_and_transfer_timeouts(self) -> None:
        """Test the primary use case: short connect, long transfer timeouts.

        This ensures quick operations like 'ls' fail fast on connection issues,
        while large file uploads/downloads have sufficient time.
        """
        # Simulate a typical configuration: quick connect, long transfer
        client = get_client("http://localhost:8000", timeout=600.0, connect_timeout=30.0)
        try:
            timeout = client.timeout
            # Connection should fail fast (30s)
            assert timeout.connect == 30.0
            # But transfers get plenty of time (10 min)
            assert timeout.read == 600.0
            assert timeout.write == 600.0
        finally:
            client.close()


class TestGetClientIntegration:
    """Integration tests for get_client with token and timeout together."""

    def test_full_configuration(self) -> None:
        """Test get_client with all parameters specified."""
        client = get_client(
            server="https://artifacts.example.com",
            token="mgp_fullconfig",
            timeout=900.0,
            connect_timeout=60.0,
        )
        try:
            assert str(client.base_url) == "https://artifacts.example.com"
            assert client.headers.get("Authorization") == "Bearer mgp_fullconfig"
            assert client.timeout.connect == 60.0
            assert client.timeout.read == 900.0
            assert client.timeout.write == 900.0
        finally:
            client.close()

    def test_client_can_be_used_as_context_manager(self) -> None:
        """Test that get_client returns a client usable as context manager."""
        with get_client("http://localhost:8000") as client:
            assert isinstance(client, httpx.Client)
