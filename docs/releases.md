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

**Important:** The hyphen is required for semver compliance. Non-hyphenated formats like `v2.0.0rc1` (without the hyphen) are rejected as invalid semver by the Docker metadata action, resulting in only the `:latest` tag being created (with no versioned tags). Always use the hyphenated format.

Release candidates allow operators to test major changes before they reach stable releases.

## Release Process

### Step 1: Update Version

Update the version in `pyproject.toml`:

**For stable releases:**
```toml
[project]
version = "X.Y.Z"  # No 'v' prefix
```

**For release candidates:**
```toml
[project]
version = "X.Y.Z-rcN"  # No 'v' prefix, hyphen required before 'rc'
```

**Note:** The version in `pyproject.toml` does NOT include the `v` prefix. The git tag will have the `v` prefix (e.g., git tag `v1.0.0` corresponds to `version = "1.0.0"` in `pyproject.toml`).

### Step 2: Commit and Tag

```bash
# Commit the version change
git add pyproject.toml
git commit -m "Release vX.Y.Z"

# Create and push the tag
git tag vX.Y.Z
git push origin HEAD vX.Y.Z
```

**Important:** The tag version (minus the leading `v` prefix) must match the version in `pyproject.toml` exactly.

### Step 3: CI/CD Automation

The GitHub Actions release workflow automatically:

1. Validates the tag matches `pyproject.toml`
2. Waits for CI on the tagged commit and stops unless every required check
   passed (`scripts/release_require_ci.sh`). Those checks run on pushes to
   `default`, so tag a commit that is on `default`.
3. Builds multi-architecture container images (amd64, arm64) and pushes the
   `<version>` and `<version>-bundled` tags to `ghcr.io/southwestccdc/magpie`
4. Renders the release assets (see [Release Assets](#release-assets))
5. Verifies the pushed digest from those exact files
   (`scripts/release_verify.sh`; each check is a subcommand you can run
   locally):
   - the assets match `SHA256SUMS` and pin the pushed digest;
   - restricted start (non-root, read-only root filesystem, no
     capabilities) on amd64 and arm64;
   - plain compose on fresh data, and the installer's install, checks and
     `uninstall --purge`;
   - an upgrade from the previous release, with plain compose and with the
     installer.
6. Only then moves the rolling tags (stable releases only) and creates the
   GitHub Release with the assets and an auto-generated changelog

If verification fails, the `<version>` image tags exist but nothing points
users at them: `latest` doesn't move and there is no GitHub Release. Fix
forward with the next version.

## Container Image Tags

From v0.2.0, `ghcr.io/southwestccdc/magpie` is the bundled single-container
image (`Dockerfile.bundled`). Releases up to 0.1.x published the
two-container backend image under the same names.

| Git Tag | Container Tags |
|---------|----------------|
| `v1.0.0` | `1.0.0`, `1.0.0-bundled`, `1.0`, `1`, `latest` |
| `v1.0.1` | `1.0.1`, `1.0.1-bundled`, `1.0`, `1`, `latest` |
| `v2.0.0-rc1` | `2.0.0-rc1`, `2.0.0-rc1-bundled` |
| `v1.0.2` cut after `v2.0.0` | `1.0.2`, `1.0.2-bundled`, `1.0` |

- `<version>-bundled` is an alias of `<version>`, kept because the installer
  and the 0.2.0 release candidates pin it.
- Release candidates get only their exact version tags. They never move
  `latest`, `<major>` or `<major>.<minor>`.
- A rolling tag (`latest`, `<major>`, `<major>.<minor>`) always points at
  the highest published stable version in its scope. A hotfix on an older
  line never pulls `latest` or `<major>` back onto it. After each stable
  release's image is pushed, the workflow's `promote` job reconciles every
  alias (`scripts/release_promote.sh`).

## Release Assets

Every GitHub release (RC and final) attaches:

| File | What |
|------|------|
| `docker-compose.yml` | The canonical compose file, with the image default pinned to this release by digest (`magpie:<version>@sha256:...`) |
| `env.example` | The configuration template (`.env.example` in the repo; GitHub drops leading dots from asset names), naming the same pinned image |
| `magpie-deploy.sh` | The installer, with `HARDCODED_VERSION` set to this release |
| `SHA256SUMS` | Checksums of the three files above |

To deploy a release without cloning the repository:

```bash
V=v0.2.0
base=https://github.com/SouthwestCCDC/magpie/releases/download/$V
curl -fsSLO "$base/docker-compose.yml" -fsSLO "$base/env.example" -fsSLO "$base/SHA256SUMS"
# Verify exactly the files you downloaded; a missing one fails the check.
grep -E ' (docker-compose\.yml|env\.example)$' SHA256SUMS | sha256sum -c
cp env.example .env
# env.example runs magpie as uid 10001 (MAGPIE_UID/MAGPIE_GID); see
# installation.md "Runtime user" before reusing data written as another uid.
# Required: choose how the first-boot admin token is delivered (file, exec,
# discard or stdout). The container refuses to start without it.
echo 'MAGPIE_ADMIN_TOKEN_SINK=file' >> .env   # then edit the rest of .env
docker compose up -d
```

`MAGPIE_IMAGE` in `.env` still overrides the pinned default. The assets are
rendered by `scripts/render_release_assets.sh`.

## Tag Mutability Policy

### Immutable Tags

`1.0.1`, `1.0.1-bundled`, `2.0.0-rc1`: these never change once pushed.

**Use for:** production deployments that need a guaranteed, unchanging
reference: `docker pull ghcr.io/southwestccdc/magpie:1.0.1`

### Mutable Tags

- `1.0` follows the newest stable `1.0.x`.
- `1` follows the newest stable `1.x.y`.
- `latest` follows the newest stable release overall.

**Use for:** development and testing where you want automatic updates.

### Hotfixes on a pre-0.2.0 line

A tag push runs the release workflow **as it exists at the tagged commit**.
A 0.1.x hotfix tagged from an old commit therefore runs the old workflow,
which moves `latest` and `0` unconditionally, back onto the two-container
image. Before tagging such a hotfix, port the `promote` job and
`scripts/release_promote.sh` and `scripts/release_rolling_tags.sh` onto the hotfix branch, and remove the
unconditional rolling tags from its build job.

## Upgrade Paths

### From Stable to Release Candidate

You **can** upgrade from a stable release to a release candidate for testing purposes:

```bash
docker pull ghcr.io/southwestccdc/magpie:2.0.0-rc1
```

**When to test RCs:**
- Upgrade to RCs for testing breaking changes in non-production environments
- Never deploy RCs directly to production without thorough validation
- RCs are for operators who want to verify compatibility before the stable release

**Installing an RC (or any specific release) with the installer script:**

`scripts/magpie-deploy.sh` installs whatever is on the `default` branch by
default. To install a specific tag -- an RC or an older stable release --
without waiting for `default` to carry it, pass `--release`:

```bash
sudo ./scripts/magpie-deploy.sh install --release v2.0.0-rc1
```

This clones that tag and pulls the matching `ghcr.io/southwestccdc/magpie`
image tag. It validates the tag/ref exists and does a best-effort check
that the image is published, failing clearly only when either is
confirmed missing -- a transient network issue during the check warns and
lets the actual clone/pull be the real gate. A `MAGPIE_VERSION` or
`GITHUB_REF` environment variable is honored as an override too (the
env-var form of `--release`), for scripted installs.

Mutable tags like `:2` or `:2.0` never point at an RC; to test one, pull its exact tag. For production, pin full version tags (e.g., `:1.5.0`).

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

**For major version RCs (e.g., 2.0.0-rc1), also verify:**
- [ ] Storage format migration completes successfully (if applicable)
- [ ] Existing artifacts remain accessible after upgrade
- [ ] Rollback to previous major version is possible (or documented as one-way upgrade)

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

---

*(AI-generated via Claude Code w/ Opus 4.6; container tag and release asset sections via Devin)*
