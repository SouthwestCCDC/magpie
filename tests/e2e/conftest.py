"""Shared fixtures for E2E tests using docker-compose."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Generator, TypedDict

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
    env["MAGPIE_ENABLE_TEST_ENDPOINTS"] = "true"  # Enable memory tracking endpoints for E2E tests
    # Force the published port rather than trusting docker-compose.yml's own
    # default: base_url below is hardcoded to port 8080, so if
    # MAGPIE_HTTP_PORT happens to be set to something else in the ambient
    # shell environment (e.g. a developer's own .env or exported var),
    # Compose would publish a different host port and every request in this
    # module would silently hit the wrong (or no) service.
    env["MAGPIE_HTTP_PORT"] = "8080"

    # docker-compose.override.yml is needed explicitly here (not just for
    # its dev conveniences) -- passing any -f at all disables Compose's
    # automatic merge of the override file, and the canonical
    # docker-compose.yml has no `build:` (image: only) and no
    # MAGPIE_ADMIN_TOKEN_SINK default (fail-closed by design); the
    # override file supplies both.
    compose_cmd = [
        "docker",
        "compose",
        "-f",
        str(PROJECT_ROOT / "docker-compose.yml"),
        "-f",
        str(PROJECT_ROOT / "docker-compose.override.yml"),
    ]

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

    Overrides MAGPIE_ADMIN_TOKEN_SINK=stdout for just this `docker compose
    exec` invocation so the token is scraped from stdout below, regardless of
    docker-compose.override.yml's own (file-sink) default -- docker-compose.yml
    itself is fail-closed and has no default -- this is the harness reading
    the token for its own use, not a production delivery path.
    """
    compose_cmd = [
        "docker",
        "compose",
        "-f",
        str(PROJECT_ROOT / "docker-compose.yml"),
        "-f",
        str(PROJECT_ROOT / "docker-compose.override.yml"),
    ]
    env = os.environ.copy()
    env["COMPOSE_PROJECT_NAME"] = docker_services["project_name"]

    # Run magpie-ctl init with --reset-admin-token to ensure we always get a token
    # This handles cases where isolation fails (cached volumes, incomplete cleanup)
    result = subprocess.run(
        [
            *compose_cmd,
            "exec",
            "-T",
            "-e",
            "MAGPIE_ADMIN_TOKEN_SINK=stdout",
            "magpie",
            "magpie-ctl",
            "init",
            "--reset-admin-token",
        ],
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
# 1. Test client running on host appears as different IP to Caddy (not in 172.18.0.0/24)
# 2. CIDR bypass never triggers because client IP is outside the allowed range
# 3. Tests would fail with 401 even though the feature works correctly
#
# To run CIDR tests, use the helper script (recommended):
#   ./scripts/run-cidr-tests.sh
#
# Or manually:
#   docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml up -d --build
#   docker compose exec test-runner-inside pytest tests/e2e/test_cidr_allowlist.py -k "not OutsideIP" -v
#   docker compose exec test-runner-outside pytest tests/e2e/test_cidr_allowlist.py::TestCIDRAllowListOutsideIPDenied -v
#
# =============================================================================


def _is_cidr_test_enabled() -> bool:
    """Check if CIDR testing is enabled via environment variable.

    Returns:
        True if running inside test-runner container with CIDR tests enabled.
    """
    return os.environ.get("MAGPIE_CIDR_TEST_ENABLED", "").lower() == "true"


@pytest.fixture(scope="session")
def cidr_base_url() -> str:
    """Get base URL for CIDR tests (internal Docker network URL).

    When running inside test-runner container, this returns the internal
    magpie service URL (http://magpie:8080 -- the bundled Caddy's port,
    see docker-compose.yml) which makes requests appear from Docker
    network IP.

    Raises:
        pytest.skip: If CIDR testing is not enabled.
    """
    if not _is_cidr_test_enabled():
        pytest.skip("CIDR tests require running inside test-runner container")

    # Use internal Docker service name when running in test-runner container
    return os.environ.get("MAGPIE_CIDR_TEST_BASE_URL", "http://magpie:8080")


@pytest.fixture(scope="session")
def cidr_admin_token(cidr_base_url: str) -> str:
    """Get admin token for CIDR-enabled services.

    The admin token must be initialized outside the test environment and passed
    via MAGPIE_CIDR_ADMIN_TOKEN environment variable. This is because the
    test-runner container cannot execute docker commands to run magpie-ctl init.

    Returns:
        Admin token string from environment.

    Raises:
        pytest.fail: If MAGPIE_CIDR_ADMIN_TOKEN is not set.
    """
    # Wait for service to be healthy via the bundled Caddy (cidr_base_url,
    # accessible from both networks -- see docker-compose.cidr-test.yml).
    # There is no separate backend port to fall back to: uvicorn only
    # listens on 127.0.0.1:8000 inside the container (see docker/bundled/
    # Caddyfile), so cidr_base_url is the only reachable endpoint from
    # either test-runner network.
    if not _wait_for_health(cidr_base_url, timeout=30, interval=1.0):
        pytest.fail(f"Magpie service did not become healthy at {cidr_base_url}")

    token = os.environ.get("MAGPIE_CIDR_ADMIN_TOKEN")
    if not token:
        pytest.fail(
            "MAGPIE_CIDR_ADMIN_TOKEN environment variable not set. "
            "Run 'docker compose exec -e MAGPIE_ADMIN_TOKEN_SINK=stdout magpie "
            "magpie-ctl init --reset-admin-token' "
            "and set the token in the test environment."
        )

    return token


@pytest.fixture
def cidr_http_client(cidr_base_url: str) -> Generator[httpx.Client, None, None]:
    """Create an unauthenticated HTTP client for CIDR bypass tests.

    This client makes requests from within the Docker network, so its IP
    (172.18.0.x) falls within the MAGPIE_ALLOWED_CIDRS range (172.18.0.0/24)
    configured in docker-compose.cidr-test.yml.

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


@pytest.fixture
def cidr_outside_http_client(cidr_base_url: str) -> Generator[httpx.Client, None, None]:
    """Create an HTTP client from OUTSIDE the CIDR allow-list.

    This client makes requests from the external-net Docker network (192.168.100.x),
    which is NOT in MAGPIE_ALLOWED_CIDRS (172.18.0.0/24). Requests should fail
    with 401 for protected read endpoints.

    Use this to verify that IPs outside the CIDR range are properly denied.

    Raises:
        pytest.skip: If not running in test-runner-outside container.
    """
    if not os.environ.get("MAGPIE_CIDR_OUTSIDE_TEST"):
        pytest.skip("Outside CIDR tests require running in test-runner-outside container")

    with httpx.Client(base_url=cidr_base_url, timeout=30.0) as client:
        yield client


# =============================================================================
# UPLOAD TESTING FIXTURES
# =============================================================================


class E2EServices(TypedDict):
    """Type for E2E services configuration."""

    base_url: str
    admin_token: str


@pytest.fixture(scope="session")
def e2e_services(base_url: str, admin_token: str) -> E2EServices:
    """Provide combined E2E services configuration for upload tests.

    Returns:
        Dictionary with base_url and admin_token for E2E testing.
    """
    return {"base_url": base_url, "admin_token": admin_token}
