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


# =============================================================================
# CIDR ALLOW-LIST TESTING FIXTURES
# =============================================================================
#
# These fixtures support testing CIDR allow-list bypass behavior.
# They detect whether tests are running inside Docker network (via environment
# variable) and configure accordingly.
#
# CIDR tests must run from within the Docker network to properly test the bypass
# behavior because:
# 1. Test client running on host appears as different IP to Caddy (not in 172.18.0.0/16)
# 2. CIDR bypass never triggers because client IP is outside the allowed range
# 3. Tests would fail with 401 even though the feature works correctly
#
# To run CIDR tests:
#   docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml up -d --build
#   docker compose exec test-runner pytest tests/e2e/test_cidr_allowlist.py -v
#
# =============================================================================


def _is_cidr_test_enabled() -> bool:
    """Check if CIDR testing is enabled via environment variable.

    Returns:
        True if running inside test-runner container with CIDR tests enabled.
    """
    return os.environ.get("MAGPIE_CIDR_TEST_ENABLED", "").lower() == "true"


@pytest.fixture(scope="session")
def cidr_test_enabled() -> bool:
    """Check if CIDR testing environment is available.

    Returns:
        True if tests are running inside Docker network with CIDR bypass enabled.
    """
    return _is_cidr_test_enabled()


@pytest.fixture(scope="session")
def cidr_base_url() -> str:
    """Get base URL for CIDR tests (internal Docker network URL).

    When running inside test-runner container, this returns the internal
    Caddy URL (http://caddy) which makes requests appear from Docker network IP.

    Raises:
        pytest.skip: If CIDR testing is not enabled.
    """
    if not _is_cidr_test_enabled():
        pytest.skip("CIDR tests require running inside test-runner container")

    # Use internal Docker service name when running in test-runner container
    return os.environ.get("MAGPIE_CIDR_TEST_BASE_URL", "http://caddy")


@pytest.fixture(scope="session")
def cidr_admin_token(cidr_base_url: str) -> str:
    """Get admin token for CIDR-enabled services.

    Initializes the system and returns an admin token.
    Note: For CIDR tests, we need to run magpie-ctl init via HTTP since we're
    inside the test-runner container and don't have docker access.

    This fixture makes an HTTP request to the magpie service directly (port 8000)
    to initialize the system.

    Returns:
        Admin token string.
    """
    # When running in test-runner container, we can't use docker commands
    # Instead, we'll use the magpie service's direct HTTP endpoint
    # The magpie container exposes port 8000 internally
    magpie_url = "http://magpie:8000"

    # Use magpie-ctl via HTTP to get the token
    # We'll exec the init command by making an HTTP request to trigger initialization
    # Since we can't exec directly, we'll use a workaround: create a token via the API
    # after the system is initialized (the admin token is created during first startup)

    # For now, let's try to get the admin token that was created during container startup
    # We'll need to store it or retrieve it somehow
    # Actually, the best approach is to set a known admin token via environment variable
    # or use the init endpoint if it exists

    # For CIDR tests, we'll use a pre-set admin token
    # This should be configured in the docker-compose.cidr-test.yml
    # For now, let's make an HTTP request to check if init is needed
    import time

    # Wait a moment for services to be fully ready
    time.sleep(2)

    # Try to use the health endpoint to verify connection
    try:
        health_check = httpx.get(f"{magpie_url}/health", timeout=5.0)
        if health_check.status_code != 200:
            pytest.fail(f"Magpie service not healthy: {health_check.status_code}")
    except Exception as e:
        pytest.fail(f"Cannot connect to magpie service: {e}")

    # Since we can't run magpie-ctl from inside test-runner, we need a different approach
    # Option 1: Use a pre-shared token via environment variable
    # Option 2: Have the token initialization happen outside the test
    # For now, we'll use a token that should be passed via environment or fail gracefully

    token = os.environ.get("MAGPIE_CIDR_ADMIN_TOKEN")
    if not token:
        pytest.fail(
            "MAGPIE_CIDR_ADMIN_TOKEN environment variable not set. "
            "Run 'docker compose exec magpie magpie-ctl init --reset-admin-token' "
            "and set the token in the test environment."
        )

    return token


@pytest.fixture
def cidr_http_client(cidr_base_url: str) -> Generator[httpx.Client, None, None]:
    """Create an unauthenticated HTTP client for CIDR bypass tests.

    This client makes requests from within the Docker network, so its IP
    (172.18.x.x) falls within the MAGPIE_ALLOWED_CIDRS range configured
    in docker-compose.cidr-test.yml.

    Use this to verify that allowed IPs can access read operations without auth.
    """
    with httpx.Client(base_url=cidr_base_url, timeout=30.0) as client:
        yield client


@pytest.fixture
def cidr_authenticated_client(
    cidr_base_url: str,
    cidr_admin_token: str,
) -> Generator[httpx.Client, None, None]:
    """Create an authenticated HTTP client for CIDR tests.

    Use this to set up test data (upload artifacts) before testing
    unauthenticated access via cidr_http_client.
    """
    headers = {"Authorization": f"Bearer {cidr_admin_token}"}
    with httpx.Client(base_url=cidr_base_url, headers=headers, timeout=30.0) as client:
        yield client
