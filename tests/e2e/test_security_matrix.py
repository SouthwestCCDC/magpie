"""Comprehensive security matrix E2E tests for the magpie auth surface (#308).

This suite is the systematic regression guard for two recent auth-hardening
fixes and documents the full endpoint/token authorization matrix as living
specification:

- #525: Caddy previously trusted client-supplied X-Magpie-User/X-Magpie-Scope
  headers in some code paths, letting a client forge its own identity/scope.
  The fix wraps every protected `handle` block in a `route {}` that strips
  both headers before forward_auth (or the CIDR-bypass synthetic injection)
  runs. TestForgedIdentityHeaders exercises every one of those route{}
  blocks directly.
- #526: The flush-tag endpoint used to trust the X-Magpie-Scope header for
  its admin check. The fix requires a validated admin Bearer token instead.
  TestFlushTagBearerGating exercises the three-way gate (no bearer -> 401,
  write bearer -> 403, admin bearer -> 200).

TestScopeEnforcementMatrix documents the endpoint-type/token-scope matrix
from #308's acceptance criteria as an executable specification, and
TestUnauthenticatedBeforeHandler guards against the #302 information
disclosure pattern (path existence leaking via 404 before auth runs).

Run via `just e2e` (uses the standard docker-compose.yml stack, no CIDR
allow-list). CIDR-specific forged-header coverage lives alongside the rest
of the CIDR suite in test_cidr_allowlist.py, since it requires the two-network
test-runner setup from docker-compose.cidr-test.yml.
"""

from __future__ import annotations

import httpx
import pytest

# Headers an attacker might send to try to forge identity/scope. These must
# never reach the FastAPI backend on any protected route -- Caddy strips them
# before forward_auth (or the CIDR-bypass synthetic injection) runs.
FORGED_HEADERS = {"X-Magpie-User": "attacker", "X-Magpie-Scope": "admin"}

# One entry per route{} block in the Caddyfile's protected-route section.
# Templates are formatted against the `matrix_artifact` fixture's dict.
FORGED_HEADER_ROUTES: list[tuple[str, str, str]] = [
    ("upload", "POST", "/api/v1/upload/{path}/forged-upload-probe"),
    ("artifact_list_paths", "GET", "/api/v1/artifacts"),
    ("artifact_list_versions", "GET", "/api/v1/artifacts/{path}"),
    ("artifact_info", "GET", "/api/v1/artifacts/{path}/{hash_ref}/info"),
    ("artifact_amend", "PATCH", "/api/v1/artifacts/{path}/{hash_ref}"),
    ("tag_create", "POST", "/api/v1/artifacts/{path}/{hash_ref}/tags"),
    ("tag_delete", "DELETE", "/api/v1/artifacts/{path}/tags/{tag_name}"),
    ("flush_tag", "POST", "/api/v1/tags/{tag_name}/flush?confirm_walk_filesystem=true"),
    ("tokens_list", "GET", "/api/v1/tokens"),
    ("tokens_create", "POST", "/api/v1/tokens"),
    ("tokens_delete", "DELETE", "/api/v1/tokens/nonexistent-forged-probe"),
    ("tokens_rotate", "POST", "/api/v1/tokens/nonexistent-forged-probe/rotate"),
    ("gc", "POST", "/api/v1/gc"),
    ("status", "GET", "/api/v1/status"),
    ("static_download", "GET", "/artifacts/{path}/{hash_ref}/artifact"),
]


@pytest.mark.e2e
@pytest.mark.slow
class TestForgedIdentityHeaders:
    """Regression guard for #525 across every protected route{} block.

    A client sending X-Magpie-User/X-Magpie-Scope directly (without a valid
    Bearer token) must never be treated as authenticated or authorized. This
    is the key regression guard for the route{}-wrapped strip that replaced
    19 copy-pasted forward_auth blocks.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def matrix_artifact(cls, base_url: str, admin_token: str) -> dict[str, str]:
        """Upload and tag one artifact for use by every route in the table.

        Deliberately independent of the shared function-scoped conftest
        fixtures (authenticated_client, test_artifact_content) so this can
        be class-scoped and set up once for the whole parametrized sweep.
        """
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            upload_response = client.post(
                "/api/v1/upload/security-matrix/forged-target",
                files={
                    "file": ("artifact", b"forged-header-matrix-probe", "application/octet-stream")
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert upload_response.status_code == 200, upload_response.text
            hash_ref = upload_response.json()["hash_ref"]

            tag_response = client.post(
                f"/api/v1/artifacts/security-matrix/forged-target/{hash_ref}/tags",
                json={"tag_name": "matrix-tag"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert tag_response.status_code in (200, 201), tag_response.text

        return {
            "path": "security-matrix/forged-target",
            "hash_ref": hash_ref,
            "tag_name": "matrix-tag",
        }

    @pytest.mark.parametrize(
        "route_name,method,template",
        FORGED_HEADER_ROUTES,
        ids=[r[0] for r in FORGED_HEADER_ROUTES],
    )
    def test_forged_headers_alone_are_rejected(
        self,
        http_client: httpx.Client,
        matrix_artifact: dict[str, str],
        route_name: str,
        method: str,
        template: str,
    ) -> None:
        """Verify forged X-Magpie-* headers without a Bearer token get 401.

        SECURITY CRITICAL: if this fails for any route, a client can forge
        its own identity and scope on that endpoint without ever presenting
        a valid token.
        """
        path = template.format(**matrix_artifact)
        response = http_client.request(method, path, headers=FORGED_HEADERS)
        assert response.status_code == 401, (
            f"SECURITY FAILURE: route '{route_name}' ({method} {path}) accepted forged "
            f"X-Magpie-User/X-Magpie-Scope headers without a Bearer token. "
            f"Expected 401, got {response.status_code}. Response: {response.text}"
        )

    def test_forged_admin_scope_with_read_token_does_not_grant_admin(
        self,
        http_client: httpx.Client,
        read_token: str,
    ) -> None:
        """A forged X-Magpie-Scope: admin header alongside a real read token
        must not elevate the request past what the read token actually grants."""
        response = http_client.get(
            "/api/v1/status",
            headers={
                "Authorization": f"Bearer {read_token}",
                "X-Magpie-Scope": "admin",
                "X-Magpie-User": "attacker",
            },
        )
        assert response.status_code == 403, (
            f"SECURITY FAILURE: forged X-Magpie-Scope: admin header elevated a read "
            f"token to admin access. Expected 403, got {response.status_code}"
        )

    def test_forged_admin_scope_with_write_token_cannot_flush(
        self,
        http_client: httpx.Client,
        write_token: str,
    ) -> None:
        """A forged X-Magpie-Scope: admin header alongside a real write token
        must not grant access to the admin-only flush-tag endpoint."""
        response = http_client.post(
            "/api/v1/tags/forged-scope-flush-probe/flush",
            params={"confirm_walk_filesystem": True},
            headers={
                "Authorization": f"Bearer {write_token}",
                "X-Magpie-Scope": "admin",
                "X-Magpie-User": "attacker",
            },
        )
        assert response.status_code == 403, (
            f"SECURITY FAILURE: forged X-Magpie-Scope: admin header let a write "
            f"token flush tags. Expected 403, got {response.status_code}"
        )

    def test_forged_user_header_does_not_override_real_identity(
        self,
        http_client: httpx.Client,
        write_token: str,
    ) -> None:
        """Functional check: uploaded_by must reflect the token's real name,
        never a client-forged X-Magpie-User value.

        The upload route reads X-Magpie-User to attribute the upload. Before
        #525, a client could forge this header directly since it was only
        stripped on some routes. This proves the strip is effective, not
        just that it returns the right status code.
        """
        upload_response = http_client.post(
            "/api/v1/upload/security-matrix/identity-spoof-probe",
            files={"file": ("artifact", b"identity spoof probe", "application/octet-stream")},
            headers={
                "Authorization": f"Bearer {write_token}",
                "X-Magpie-User": "attacker",
            },
        )
        assert upload_response.status_code == 200, upload_response.text

        info_response = http_client.get(
            "/api/v1/artifacts/security-matrix/identity-spoof-probe",
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert info_response.status_code == 200
        versions = info_response.json()["versions"]
        assert len(versions) >= 1
        assert versions[0]["uploaded_by"] == "test-writer", (
            f"SECURITY FAILURE: forged X-Magpie-User header overrode the real token "
            f"identity. Expected 'test-writer' (the write_token fixture's real name), "
            f"got {versions[0]['uploaded_by']!r}."
        )
        assert versions[0]["uploaded_by"] != "attacker"


@pytest.mark.e2e
@pytest.mark.slow
class TestFlushTagBearerGating:
    """Regression guard for #526: flush-tag requires a validated admin Bearer
    token. The X-Magpie-Scope header (even if genuinely set by Caddy) is no
    longer sufficient on its own.
    """

    def test_no_bearer_token_rejected(self, http_client: httpx.Client) -> None:
        """No Authorization header at all -> 401."""
        response = http_client.post(
            "/api/v1/tags/bearer-gate-no-token-probe/flush",
            params={"confirm_walk_filesystem": True},
        )
        assert response.status_code == 401

    def test_forged_admin_scope_header_without_bearer_rejected(
        self, http_client: httpx.Client
    ) -> None:
        """Forged X-Magpie-Scope: admin header with no Bearer token -> 401,
        not 200. This is the direct #526 regression case: the header alone
        must never be sufficient."""
        response = http_client.post(
            "/api/v1/tags/bearer-gate-forged-header-probe/flush",
            params={"confirm_walk_filesystem": True},
            headers={"X-Magpie-Scope": "admin", "X-Magpie-User": "attacker"},
        )
        assert response.status_code == 401, (
            f"SECURITY FAILURE: forged X-Magpie-Scope: admin header without a Bearer "
            f"token was accepted by flush-tag. Expected 401, got {response.status_code}"
        )

    def test_write_bearer_token_rejected(self, http_client: httpx.Client, write_token: str) -> None:
        """A valid write-scope Bearer token is insufficient -> 403."""
        response = http_client.post(
            "/api/v1/tags/bearer-gate-write-token-probe/flush",
            params={"confirm_walk_filesystem": True},
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert response.status_code == 403

    def test_admin_bearer_token_succeeds(self, http_client: httpx.Client, admin_token: str) -> None:
        """A valid admin-scope Bearer token succeeds -> 200.

        Uses a tag name that doesn't exist anywhere, so this is a safe
        no-op flush (count=0) rather than mutating real test data.
        """
        response = http_client.post(
            "/api/v1/tags/bearer-gate-admin-token-probe/flush",
            params={"confirm_walk_filesystem": True},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["tag_name"] == "bearer-gate-admin-token-probe"
        assert data["count"] == 0


@pytest.mark.e2e
@pytest.mark.slow
class TestScopeEnforcementMatrix:
    """Executable specification for the #308 endpoint/token authorization matrix.

    | Endpoint Type | No Token | Read | Write | Admin |
    |---------------|----------|------|-------|-------|
    | Public        | 200      | 200  | 200   | 200   |
    | Read          | 401      | 200  | 200   | 200   |
    | Write         | 401      | 403  | 200   | 200   |
    | Admin         | 401      | 403  | 403   | 200   |

    One representative, idempotent endpoint stands in for each row so the
    matrix can run repeatedly against the same fixture data without ordering
    side effects: GET /health (public), GET /api/v1/artifacts (read),
    PATCH .../{ref} to amend metadata (write), GET /api/v1/status (admin).
    """

    @pytest.fixture(scope="class")
    @classmethod
    def matrix_artifact(cls, base_url: str, admin_token: str) -> dict[str, str]:
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            upload_response = client.post(
                "/api/v1/upload/security-matrix/scope-target",
                files={"file": ("artifact", b"scope-matrix-probe", "application/octet-stream")},
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert upload_response.status_code == 200, upload_response.text
            hash_ref = upload_response.json()["hash_ref"]
        return {"path": "security-matrix/scope-target", "hash_ref": hash_ref}

    @staticmethod
    def _headers(request: pytest.FixtureRequest, token_fixture: str | None) -> dict[str, str]:
        if token_fixture is None:
            return {}
        token = request.getfixturevalue(token_fixture)
        return {"Authorization": f"Bearer {token}"}

    @pytest.mark.parametrize(
        "token_fixture,expected_status",
        [(None, 200), ("read_token", 200), ("write_token", 200), ("admin_token", 200)],
        ids=["no_token", "read_token", "write_token", "admin_token"],
    )
    def test_public_endpoint_matrix(
        self,
        http_client: httpx.Client,
        request: pytest.FixtureRequest,
        token_fixture: str | None,
        expected_status: int,
    ) -> None:
        """GET /health: accessible regardless of token presence or scope."""
        headers = self._headers(request, token_fixture)
        response = http_client.get("/health", headers=headers)
        assert response.status_code == expected_status

    @pytest.mark.parametrize(
        "token_fixture,expected_status",
        [(None, 401), ("read_token", 200), ("write_token", 200), ("admin_token", 200)],
        ids=["no_token", "read_token", "write_token", "admin_token"],
    )
    def test_read_endpoint_matrix(
        self,
        http_client: httpx.Client,
        request: pytest.FixtureRequest,
        token_fixture: str | None,
        expected_status: int,
    ) -> None:
        """GET /api/v1/artifacts: requires at least read scope."""
        headers = self._headers(request, token_fixture)
        response = http_client.get("/api/v1/artifacts", headers=headers)
        assert response.status_code == expected_status

    @pytest.mark.parametrize(
        "token_fixture,expected_status",
        [(None, 401), ("read_token", 403), ("write_token", 200), ("admin_token", 200)],
        ids=["no_token", "read_token", "write_token", "admin_token"],
    )
    def test_write_endpoint_matrix(
        self,
        http_client: httpx.Client,
        request: pytest.FixtureRequest,
        matrix_artifact: dict[str, str],
        token_fixture: str | None,
        expected_status: int,
    ) -> None:
        """PATCH .../{ref} (metadata amend): requires write or admin scope.

        Idempotent: re-amending the same artifact's source_uri is safe to
        repeat across parametrized cases in any order.
        """
        headers = self._headers(request, token_fixture)
        path = f"/api/v1/artifacts/{matrix_artifact['path']}/{matrix_artifact['hash_ref']}"
        response = http_client.patch(
            path,
            json={"source_uri": f"probe://{token_fixture or 'anonymous'}"},
            headers=headers,
        )
        assert response.status_code == expected_status

    @pytest.mark.parametrize(
        "token_fixture,expected_status",
        [(None, 401), ("read_token", 403), ("write_token", 403), ("admin_token", 200)],
        ids=["no_token", "read_token", "write_token", "admin_token"],
    )
    def test_admin_endpoint_matrix(
        self,
        http_client: httpx.Client,
        request: pytest.FixtureRequest,
        token_fixture: str | None,
        expected_status: int,
    ) -> None:
        """GET /api/v1/status: requires admin scope."""
        headers = self._headers(request, token_fixture)
        response = http_client.get("/api/v1/status", headers=headers)
        assert response.status_code == expected_status


@pytest.mark.e2e
@pytest.mark.slow
class TestUnauthenticatedBeforeHandler:
    """Guards against the #302 information-disclosure pattern: path
    existence must never be observable (via 404 vs 401) before auth runs.
    """

    def test_nonexistent_artifact_list_returns_401_not_404(self, http_client: httpx.Client) -> None:
        response = http_client.get("/api/v1/artifacts/does/not/exist/anywhere")
        assert response.status_code == 401, (
            "Unauthenticated requests for a nonexistent artifact path must fail auth "
            "(401) before path resolution can leak existence via 404."
        )

    def test_nonexistent_artifact_info_returns_401_not_404(self, http_client: httpx.Client) -> None:
        response = http_client.get("/api/v1/artifacts/does/not/exist/@deadbeef/info")
        assert response.status_code == 401

    def test_nonexistent_static_artifact_returns_401_not_404(
        self, http_client: httpx.Client
    ) -> None:
        response = http_client.get("/artifacts/does/not/exist/@deadbeef/artifact")
        assert response.status_code == 401

    def test_nonexistent_admin_route_child_returns_401_not_404(
        self, http_client: httpx.Client
    ) -> None:
        """Even a bogus tag name under the admin-gated flush path must 401
        before any filesystem walk or 404 could occur."""
        response = http_client.post(
            "/api/v1/tags/this-tag-does-not-exist-anywhere/flush",
            params={"confirm_walk_filesystem": True},
        )
        assert response.status_code == 401
