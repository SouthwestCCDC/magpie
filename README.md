# Magpie

Lightweight versioned artifact storage with content-addressing and mutable tags.

## Why Magpie?

Magpie solves the artifact distribution problem for teams that need:

- **Simple versioned storage** without complex object storage setup or package registry overhead
- **Content deduplication** - identical files are stored once regardless of how many tags point to them
- **Immutable artifacts with mutable pointers** - tags like `latest` or `stable` can move while the underlying content never changes
- **Provenance tracking** - know where artifacts came from via source URI metadata
- **Straightforward operation** - single binary, minimal dependencies, filesystem-based storage

Built for distributing build artifacts, container images, deployment packages, and other binary assets across infrastructure without the complexity of S3, artifactory, or package-specific registries.

## Features

- **Content-addressed storage**: Artifacts stored by SHA-256 hash with automatic deduplication
- **Mutable tags**: Human-readable tags (`latest`, `stable`, `v1.0`) pointing to specific blobs
- **Provenance tracking**: Optional source URI metadata for traceability
- **Dual authentication**: Bearer tokens for API/CLI access, optional Authentik SSO for browser access
- **Garbage collection**: Automatic cleanup of untagged artifacts past retention period
- **Observability**: Sentry integration and OpenTelemetry support

## Quick Start

### Try It Locally

```bash
# Clone and start
git clone https://github.com/SouthwestCCDC/magpie
cd magpie
docker compose up -d

# Get the admin token from logs
docker compose logs magpie | grep "ADMIN TOKEN"

# Configure client
export MAGPIE_SERVER=http://localhost:8080
export MAGPIE_TOKEN=mgp_ADMIN_...

# Install client (requires uv: https://docs.astral.sh/uv/)
uv pip install git+https://github.com/SouthwestCCDC/magpie

# Upload and download
echo "Hello Magpie" > hello.txt
magpie push hello.txt --to demo/greeting
magpie get demo/greeting:latest
magpie ls demo/greeting
```

Server runs at `http://localhost:8080` (change via `MAGPIE_HTTP_PORT`).

### Production Deployment

```bash
# Set domain and storage location
export MAGPIE_DOMAIN=magpie.example.com
export MAGPIE_DATA_DIR=/path/to/persistent/storage

# Start with TLS (auto-provisioned via Let's Encrypt)
docker compose -f docker-compose.prod.yml up -d

# Get admin token
docker compose -f docker-compose.prod.yml logs magpie | grep "ADMIN TOKEN"

# Create additional tokens
docker compose -f docker-compose.prod.yml exec magpie \
  magpie-ctl token create --name ci-deployer --scope write
```

See [Installation Guide](docs/installation.md) for detailed deployment options.

## Documentation

- [Installation Guide](docs/installation.md) - Server deployment and client setup
- [User Guide](docs/user-guide.md) - Complete CLI and API reference
- [Production Checklist](docs/production-checklist.md) - Pre-deployment verification
- [Backup and Restore Guide](docs/backup-restore.md) - Backup procedures and disaster recovery
- [Documentation Index](docs/index.md) - Full documentation overview

## CLI Usage

```bash
# Upload an artifact
magpie push myfile.tar.gz --to images/ubuntu

# Download an artifact
magpie get images/ubuntu:latest

# List versions
magpie ls images/ubuntu

# Create a tag pointing to a specific version
magpie tag images/ubuntu:latest --as stable

# Get download URL for scripting
magpie url images/ubuntu:stable
```

See [User Guide](docs/user-guide.md) for complete command reference.

## Development

```bash
# Install dependencies
uv sync

# Run server locally
uv run uvicorn magpie.server.app:app --reload

# Run CLI commands
uv run magpie --help
uv run magpie-ctl --help

# Run with Docker Compose (Caddy + Magpie)
docker compose up --build

# Run tests
uv run pytest tests/unit/ -v
uv run ruff check src/ tests/
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for development workflow.

## Project Structure

```
src/magpie/
  server/     # FastAPI application
  storage/    # Filesystem operations (blobs, manifests, tags)
  cli/        # Client CLI (magpie)
  ctl/        # Server admin CLI (magpie-ctl)
  auth/       # Token authentication and database
```

## CLI Commands

### Client (`magpie`)

| Command | Description |
|---------|-------------|
| `push` | Upload an artifact to the server |
| `get` | Download an artifact |
| `ls` | List artifact versions |
| `info` | Show artifact metadata |
| `url` | Get download URL for scripting |
| `tag` | Create or update a tag |
| `untag` | Remove a tag |
| `amend` | Update artifact metadata |
| `gc` | Run garbage collection (admin) |
| `flush-tag` | Remove tag globally (admin) |
| `status` | Check server health and connectivity (admin) |
| `token` | Create tokens (admin) |
| `config` | Show resolved configuration |
| `version` | Show client version |

### Server Admin (`magpie-ctl`)

| Command | Description |
|---------|-------------|
| `init` | Initialize storage and create admin token |
| `token` | Manage tokens (create, list, revoke) |
| `gc` | Run garbage collection |
| `flush-tag` | Remove tag globally |
| `sync` | S3 backup operations (to-s3, from-s3, gc-s3) |
| `version` | Show server version |

## Testing

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=magpie

# Run linting
uv run ruff check .
uv run ruff format --check .
```

## Releases

Releases are automated via GitHub Actions when a version tag is pushed.

### Release Process

1. Update version in `pyproject.toml`
2. Commit the change:
   ```bash
   git add pyproject.toml
   git commit -m "Release vX.Y.Z"
   ```
3. Create and push the tag:
   ```bash
   git tag vX.Y.Z
   git push origin HEAD vX.Y.Z
   ```

The release workflow will:
- Validate the tag matches `pyproject.toml` version
- Build multi-arch container images (amd64, arm64)
- Push to `ghcr.io/southwestccdc/magpie`
- Create a GitHub Release with auto-generated changelog
- Attach `scripts/magpie-deploy.sh` as a release asset

### Container Tags

| Git Tag | Container Tags |
|---------|----------------|
| `v1.2.0` | `1.2.0`, `1.2`, `1`, `latest` |
| `v1.2.1` | `1.2.1`, `1.2`, `1`, `latest` |
| `v2.0.0-rc1` | `2.0.0-rc1` (no `latest`) |

**Note on tag mutability**

- Full version tags (`MAJOR.MINOR.PATCH`, e.g. `1.0.1`) are immutable and always point to the
  exact release that created them.
- Major/minor tags (`MAJOR`, `MAJOR.MINOR`, e.g. `1`, `1.0`) are **mutable** and will be moved
  to the latest patch release in that series (e.g. `1.0` and `1` move from `1.0.0` to `1.0.1`).
- `latest` is also **mutable** and always points to the most recent stable release.

If you require a non-changing reference for deployments, pin to the full version tag
(e.g. `ghcr.io/southwestccdc/magpie:1.0.1`).

### Installing from Release

```bash
# Server: Use container from registry
docker pull ghcr.io/southwestccdc/magpie:latest

# Client: Install from git tag
uv pip install git+https://github.com/SouthwestCCDC/magpie@vX.Y.Z
```

---
*Documentation improved with AI assistance (Claude Code w/ Sonnet 4.5).*
