# Release and Version Management

This guide covers Magpie's release process, versioning scheme, and release candidate policy.

## Versioning Scheme

Magpie follows **Semantic Versioning (SemVer)** for all releases: `MAJOR.MINOR.PATCH`

- **MAJOR** - Breaking changes to API, storage format, or behavior
- **MINOR** - New features (backward-compatible)
- **PATCH** - Bug fixes and maintenance releases

### Release Candidate Format

Release candidates use the `-rcX` format (where X is a number starting at 1):

```
v2.0.0-rc1    # First release candidate for 2.0.0
v2.0.0-rc2    # Second release candidate for 2.0.0 (fixes issues found in rc1)
v2.0.0        # Final stable release
```

Release candidates allow operators to test major changes before they reach stable releases.

## Release Process

### Step 1: Update Version

Update the version in `pyproject.toml`:

```toml
[project]
version = "X.Y.Z"  # For stable releases
version = "X.Y.Z-rcN"  # For release candidates
```

### Step 2: Commit and Tag

```bash
# Commit the version change
git add pyproject.toml
git commit -m "Release vX.Y.Z"

# Create and push the tag
git tag vX.Y.Z
git push origin HEAD vX.Y.Z
```

**Important:** The tag must match the version in `pyproject.toml` exactly.

### Step 3: CI/CD Automation

The GitHub Actions release workflow automatically:

1. Validates the tag matches `pyproject.toml`
2. Builds multi-architecture container images (amd64, arm64)
3. Pushes images to `ghcr.io/southwestccdc/magpie`
4. Creates a GitHub Release with auto-generated changelog
5. Attaches release assets (e.g., `scripts/magpie-deploy.sh`)

## Container Image Tags

### Stable Release Tags

Full stable releases receive multiple container tags:

| Git Tag | Container Tags | Use Case |
|---------|----------------|----------|
| `v1.0.0` | `1.0.0`, `1.0`, `1`, `latest` | Latest stable release |
| `v1.0.1` | `1.0.1`, `1.0`, `1`, `latest` | Patch release (updates mutable tags) |
| `v2.0.0` | `2.0.0`, `2.0`, `2`, `latest` | Major release (updates all tags) |

### Release Candidate Tags

Release candidates receive a single immutable tag with the full version:

| Git Tag | Container Tags | Behavior |
|---------|----------------|----------|
| `v2.0.0-rc1` | `2.0.0-rc1` | **Immutable** - does not update mutable tags |
| `v2.0.0-rc2` | `2.0.0-rc2` | **Immutable** - does not update mutable tags |

**Key differences:**
- Release candidates **do not** get `latest`, major, or minor tags
- Each RC is identified by its full version including the `-rcX` suffix
- RCs are immutable - the tag will never change
- Stable release candidates help validate critical changes before final release

## Tag Mutability Policy

### Immutable Tags (Full Version Only)

These tags always point to the exact same release:
- `1.0.1` - Never changes once created
- `2.0.0-rc1` - Never changes once created
- `1.5.0` - Never changes once created

**Use for:** Production deployments where you need a guaranteed, unchanging reference.

**Pull command:** `docker pull ghcr.io/southwestccdc/magpie:1.0.1`

### Mutable Tags (Major and Minor)

These tags move to the latest patch release in their series:
- `1.0` moves from `1.0.0` → `1.0.1` → `1.0.2`
- `1` moves from `1.0.0` → `1.5.0` → `2.0.0`
- `latest` moves to the newest stable release

**Use for:** Development and testing where you want automatic updates.

**Note:** RCs never update mutable tags. For example, releasing `v2.0.0-rc1` does not move the `2` or `latest` tags.

## Upgrade Paths

### From Stable to Release Candidate

You **can** upgrade from a stable release to a release candidate:

```bash
docker pull ghcr.io/southwestccdc/magpie:2.0.0-rc1
```

This allows early testing of major changes before the final release.

### From Release Candidate to Stable

When the RC is released as stable (e.g., `v2.0.0`), you should upgrade:

```bash
# Upgrade from RC to stable
docker pull ghcr.io/southwestccdc/magpie:2.0.0
```

### Between Release Candidates

You can upgrade between RCs (e.g., `rc1` → `rc2`) if issues are found and fixed:

```bash
docker pull ghcr.io/southwestccdc/magpie:2.0.0-rc2
```

## Client Installation

### Server: Container Registry

Pull the latest stable release:

```bash
docker pull ghcr.io/southwestccdc/magpie:latest
```

Or pin to a specific version:

```bash
docker pull ghcr.io/southwestccdc/magpie:1.0.1
```

### Client: Direct from Git Tag

Install the client CLI from a specific release tag:

```bash
uv pip install git+https://github.com/SouthwestCCDC/magpie@vX.Y.Z
```

## Release Candidate Testing Checklist

When testing a release candidate, verify:

- [ ] New features work as documented
- [ ] Breaking changes are intentional and necessary
- [ ] API endpoints respond correctly
- [ ] CLI commands execute properly
- [ ] Storage operations (push, get, tag, untag) work correctly
- [ ] Garbage collection completes without errors
- [ ] Database migrations (if applicable) succeed
- [ ] Upgrade from previous stable version works smoothly
- [ ] Performance is acceptable for your use case
- [ ] Monitoring and error reporting work (Sentry, OpenTelemetry)

## Feedback on Release Candidates

Found a problem with a release candidate? Open an issue with:

- The exact version tested (e.g., `v2.0.0-rc1`)
- Steps to reproduce the problem
- Your environment (OS, Docker version, Python version if applicable)
- Logs or error messages
- Workarounds (if found)

Release candidate feedback helps ensure the final stable release is production-ready.

## Related Documentation

- [Installation Guide](installation.md) - Server deployment and client setup
- [Production Checklist](production-checklist.md) - Pre-deployment verification
- [User Guide](user-guide.md) - CLI usage reference
