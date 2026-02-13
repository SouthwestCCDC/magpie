# API Compatibility Policy

This document defines the API compatibility contract for Magpie, including what constitutes a breaking change and the versioning guarantees that contributors and operators can rely on.

## Pre-1.0 Stability

**Current status:** Magpie is at version `0.1.x` (pre-1.0).

During the `0.x` series, API changes are expected as we refine the interface based on operational experience. However, we still aim to minimize breaking changes:

- Minor version bumps (`0.1` → `0.2`) may include carefully considered breaking changes if necessary
- Such changes will be documented in release notes with migration guides
- After reaching `1.0`, the full SemVer contract applies strictly

**Note:** Semantic Versioning allows complete instability during `0.x` development, but we aim for additive-only changes to support early adopters.

## Core Principle: Additive-Only Changes

Within a major version, the API follows an **additive-only** compatibility policy. Old clients must continue to work without modification when the server is upgraded to a newer minor or patch version.

### Safe Changes (Non-Breaking)

The following changes are **safe** and can be made in minor releases:

- **New optional response fields**: Safe when clients use lenient parsers. The Magpie CLI uses Pydantic's default `extra='ignore'` and will silently ignore unknown response fields. Clients with strict validators (e.g., `extra='forbid'` or strict JSON schemas) may error on unknown fields.
- **New endpoints**: Old clients don't call endpoints they don't know about.
- **New optional request parameters**: Existing requests without the new parameter continue to work.
- **New optional query parameters**: Existing requests without the new parameter continue to work.
- **Relaxing validation rules**: Accepting more input is backward compatible (except when fixing security vulnerabilities).
- **Adding new enum values**: If handled with proper defaults in existing code.

### Breaking Changes (Require Major Version)

The following changes are **breaking** and require a new API version (`/api/v2/`):

- **Removing an endpoint**: Old clients calling the endpoint will fail.
- **Removing or renaming a response field**: Old clients expecting the field will break.
- **Adding a required request field**: Old clients not sending the field will fail.
- **Changing field types**: Old clients may send or expect incompatible data.
- **Renaming request parameters**: Old clients using the old name will fail.
- **Tightening validation rules**: Previously accepted input may now be rejected (exception: fixing security vulnerabilities like path traversal may justify breaking changes in minor versions).
- **Changing endpoint paths**: Old clients calling the old path will fail.
- **Changing HTTP methods**: Old clients using the wrong method will fail.

## Semantic Versioning (SemVer)

Magpie follows [Semantic Versioning](https://semver.org/) with the following semantics:

### PATCH (0.1.x)

**Bug fixes only.** No API schema changes or new features.

- Fix incorrect behavior
- Security patches
- Performance improvements
- Documentation updates

### MINOR (0.x.0)

**Backward-compatible additions.** Old clients continue to work.

- New API endpoints
- New optional response fields (with sensible defaults)
- New optional request parameters
- New CLI commands or flags
- Internal refactoring with no API impact

### MAJOR (x.0.0)

**Breaking API changes.** Requires coordinated upgrade planning.

- Remove or rename endpoints
- Remove or rename fields
- Add required request fields
- Change field types or semantics
- Introduce `/api/v2/` with deprecation window for `/api/v1/`

## Version Coupling

The CLI and server ship from the same repository with matching version numbers. This simplifies compatibility reasoning:

- **Recommendation: Deploy CLI and server at matching minor versions**: CLI `0.2.x` with server `0.2.x` ensures compatibility. Version checking is not currently enforced by the CLI (tracked in issue #451 for v0.1.3 milestone).
- **Patch versions are interchangeable within a minor**: CLI `0.2.1` should work with server `0.2.3` and vice versa
- **Cross-minor compatibility is not guaranteed**: CLI `0.2.x` may not work with server `0.3.x`

**Recommendation:** Deploy the CLI and server together using the same container image tag or release artifact.

## API Versioning in URLs

The current API uses the `/api/v1/` prefix for all endpoints:

```
POST /api/v1/upload/{path}
GET  /api/v1/artifacts/{path}/{ref}/info
POST /api/v1/artifacts/{path}/{ref}/tags
DELETE /api/v1/artifacts/{path}/tags/{tag_name}
PATCH /api/v1/artifacts/{path}/{ref}
GET  /api/v1/artifacts
GET  /api/v1/artifacts/{path}
POST /api/v1/gc
GET  /api/v1/auth/validate
POST /api/v1/tokens
GET  /api/v1/tokens
DELETE /api/v1/tokens/{name}
POST /api/v1/tokens/{name}/rotate
POST /api/v1/tags/{tag_name}/flush
GET  /api/v1/status
```

If a breaking change is required, we would introduce `/api/v2/` and run both versions side-by-side during a deprecation window.

## Contributor Checklist

Before making API changes, ask yourself:

### Adding a Response Field?

✅ **Safe for minor release** if the field is:
- Optional with a sensible default (nullable or with a default value)
- Documented in API reference and changelog

Example:
```python
class ArtifactInfoResponse(BaseModel):
    hash: str
    uploaded_at: datetime
    # Safe to add in minor version:
    retention_days: int | None = None  # New optional field
```

### Removing or Renaming a Field?

❌ **Breaking change** - requires major version bump

- Discuss on GitHub issue before proceeding
- Plan `/api/v2/` introduction
- Document migration path

### Adding a Required Request Field?

❌ **Breaking change** - old clients don't send it

- If truly needed, introduce as optional first in one minor release
- Make it required in next major version with deprecation warning

### Adding an Optional Request Parameter?

✅ **Safe for minor release** if:
- Existing behavior is preserved when parameter is omitted
- Default value maintains current functionality

Example:
```python
@router.get("/api/v1/artifacts/{path}")
async def list_artifacts(
    path: str,
    include_deleted: bool = False,  # New optional param - safe
):
    ...
```

### Changing Validation Rules?

- **Relaxing (accepting more)**: ✅ Safe for minor release
- **Tightening (rejecting more)**: ❌ Breaking change (exception: fixing security vulnerabilities like path traversal may justify breaking changes in minor versions)

## Testing for Compatibility

When adding optional fields or parameters:

1. **Add tests for old client behavior**: Ensure existing tests still pass without modification
2. **Add tests for new behavior**: Cover the new field/parameter explicitly
3. **Document in changelog**: Note the new feature and its optional nature

Example test structure:
```python
def test_artifact_info_without_new_field():
    """Old clients should work without the new field being required."""
    response = client.get("/api/v1/artifacts/foo/bar:latest/info")
    assert "hash" in response.json()
    # New field IS in response, but old clients don't break if they ignore it

def test_artifact_info_with_new_field():
    """New field is present and has expected value."""
    response = client.get("/api/v1/artifacts/foo/bar:latest/info")
    data = response.json()
    assert "retention_days" in data
    assert data["retention_days"] is None or isinstance(data["retention_days"], int)
```

## See Also

- [User Guide](user-guide.md) - CLI usage and API examples
- [CONTRIBUTING.md](../CONTRIBUTING.md) - Pull request workflow and guidelines
- [Installation Guide](installation.md) - Version selection and upgrade procedures

---

*AI-generated documentation via Claude Code w/ Opus 4.6*
