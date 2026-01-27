"""Shared fixtures for E2E tests using docker-compose."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Generator

import httpx
import pytest

# Project root directory (where docker-compose.yml is located)
PROJECT_ROOT = Path(__file__).parent.parent.parent.absolute()


def _wait_for_health(base_url: str, timeout: int = 60, interval: float = 1.0) -> bool:
    """Wait for the health endpoint to respond.

    Args:
        base_url: Base URL of the service.
        timeout: Maximum time to wait in seconds.
        interval: Time between checks in seconds.

    Returns:
        True if service became healthy, False if timeout.
    """
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            response = httpx.get(f"{base_url}/health", timeout=5.0)
            if response.status_code == 200:
                return True
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        time.sleep(interval)
    return False


def _extract_admin_token(output: str) -> str | None:
    """Extract admin token from magpie-ctl init output.

    Args:
        output: Command output containing the token.

    Returns:
        The extracted token or None if not found.
    """
    # Token is between === lines, look for pattern like mgp_...
    lines = output.strip().split("\n")
    for line in lines:
        line = line.strip()
        # Token starts with mgp_ prefix
        if line.startswith("mgp_"):
            return line
    return None


@pytest.fixture(scope="session")
def docker_compose_project_name() -> str:
    """Generate unique project name for test isolation."""
    return f"magpie_e2e_test_{os.getpid()}"


@pytest.fixture(scope="session")
def docker_services(
    docker_compose_project_name: str,
) -> Generator[dict[str, str], None, None]:
    """Start docker-compose services for E2E tests.

    Starts services, waits for health checks, yields service info, then cleans up.

    Test isolation is achieved by:
    - Using a unique COMPOSE_PROJECT_NAME per test session
    - Mounting a temporary directory as MAGPIE_DATA_DIR
    - Cleaning up all containers, volumes, and temp data after tests
    """
    # Create temporary data directory for test isolation
    # This ensures tests don't affect real data and start fresh each session
    temp_data_dir = Path(tempfile.mkdtemp(prefix="magpie_e2e_"))
    artifacts_dir = temp_data_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)

    env = os.environ.copy()
    env["COMPOSE_PROJECT_NAME"] = docker_compose_project_name
    # Use the temp directory for data isolation - this overrides the default ./data
    # The temp directory will contain both artifacts/ subdirectory and magpie.db file
    env["MAGPIE_DATA_DIR"] = str(temp_data_dir)

    compose_cmd = ["docker", "compose", "-f", str(PROJECT_ROOT / "docker-compose.yml")]

    try:
        # Build services
        subprocess.run(
            [*compose_cmd, "build"],
            cwd=PROJECT_ROOT,
            env=env,
            check=True,
            capture_output=True,
        )

        # Start services with isolated temp data directory via MAGPIE_DATA_DIR env var
        subprocess.run(
            [
                *compose_cmd,
                "up",
                "-d",
                "--wait",
            ],
            cwd=PROJECT_ROOT,
            env=env,
            check=True,
            capture_output=True,
        )

        # Wait for health endpoint
        base_url = "http://localhost:8080"
        if not _wait_for_health(base_url):
            # Get logs for debugging
            logs_result = subprocess.run(
                [*compose_cmd, "logs"],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            pytest.fail(f"Services failed to become healthy.\nLogs:\n{logs_result.stdout}")

        yield {
            "base_url": base_url,
            "project_name": docker_compose_project_name,
        }

    finally:
        # Cleanup: stop and remove containers
        subprocess.run(
            [*compose_cmd, "down", "-v", "--remove-orphans"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
        )
        # Clean up temp data directory
        shutil.rmtree(temp_data_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def admin_token(docker_services: dict[str, str]) -> str:
    """Get admin token from initialized system.

    Runs magpie-ctl init inside the container to get the admin token.
    Uses --reset-admin-token to ensure we always get a fresh token, even if
    one already exists (e.g., from cached Docker volumes or incomplete cleanup).
    """
    compose_cmd = [
        "docker",
        "compose",
        "-f",
        str(PROJECT_ROOT / "docker-compose.yml"),
    ]
    env = os.environ.copy()
    env["COMPOSE_PROJECT_NAME"] = docker_services["project_name"]

    # Run magpie-ctl init with --reset-admin-token to ensure we always get a token
    # This handles cases where isolation fails (cached volumes, incomplete cleanup)
    result = subprocess.run(
        [*compose_cmd, "exec", "-T", "magpie", "magpie-ctl", "init", "--reset-admin-token"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        pytest.fail(f"Failed to initialize: {result.stderr}")

    token = _extract_admin_token(result.stdout)
    if not token:
        pytest.fail(f"Could not extract admin token from output:\n{result.stdout}")

    return token


@pytest.fixture(scope="session")
def base_url(docker_services: dict[str, str]) -> str:
    """Get the base URL for API requests."""
    return docker_services["base_url"]


@pytest.fixture
def auth_headers(admin_token: str) -> dict[str, str]:
    """Get authorization headers with admin token."""
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def http_client(base_url: str) -> Generator[httpx.Client, None, None]:
    """Create an HTTP client for API requests."""
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        yield client


@pytest.fixture
def authenticated_client(
    base_url: str,
    admin_token: str,
) -> Generator[httpx.Client, None, None]:
    """Create an authenticated HTTP client."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    with httpx.Client(base_url=base_url, headers=headers, timeout=30.0) as client:
        yield client


def create_token_via_api(
    client: httpx.Client,
    admin_token: str,
    name: str,
    scope: str,
) -> str:
    """Create a token via the API.

    Args:
        client: HTTP client instance.
        admin_token: Admin token for authorization.
        name: Token name.
        scope: Token scope (read, write, admin).

    Returns:
        The created token string.
    """
    response = client.post(
        "/api/v1/tokens",
        json={"name": name, "scope": scope},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    response.raise_for_status()
    return response.json()["token"]


@pytest.fixture(scope="session")
def read_token(base_url: str, admin_token: str) -> str:
    """Create a read-only token for testing.

    Session-scoped to avoid duplicate token name errors across tests.
    """
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        return create_token_via_api(client, admin_token, "test-reader", "read")


@pytest.fixture(scope="session")
def write_token(base_url: str, admin_token: str) -> str:
    """Create a write token for testing.

    Session-scoped to avoid duplicate token name errors across tests.
    """
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        return create_token_via_api(client, admin_token, "test-writer", "write")


def upload_artifact(
    client: httpx.Client,
    path: str,
    content: bytes,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Upload artifact content using multipart form data.

    Args:
        client: HTTP client instance.
        path: Artifact path (e.g., "e2e-tests/my-artifact").
        content: Binary content to upload.
        headers: Optional additional headers (e.g., Authorization).

    Returns:
        HTTP response from the upload endpoint.
    """
    return client.post(
        f"/api/v1/upload/{path}",
        files={"file": ("artifact", content, "application/octet-stream")},
        headers=headers,
    )


@pytest.fixture
def test_artifact_content() -> bytes:
    """Generate test artifact content."""
    return b"E2E test artifact content - version 1.0"


@pytest.fixture
def test_artifact_path() -> str:
    """Generate a test artifact path."""
    return "e2e-tests/test-artifact"
