# Magpie

Content-addressed artifact storage with mutable tags for distributing build artifacts,
container images, and other binary assets.

## Features

- **Content-addressed storage**: Artifacts stored by SHA-256 hash with automatic deduplication
- **Mutable tags**: Human-readable tags (`latest`, `stable`, `v1.0`) pointing to specific blobs
- **Provenance tracking**: Optional source URI metadata for traceability
- **Dual authentication**: Bearer tokens for API/CLI access, optional Authentik SSO for browser access
- **Garbage collection**: Automatic cleanup of untagged artifacts past retention period
- **Observability**: Sentry integration and OpenTelemetry support

## Quick Start

```bash
# Configure the client
export MAGPIE_SERVER=https://magpie.example.com
export MAGPIE_TOKEN=mgp_your_token

# Upload an artifact
magpie push myfile.tar.gz --to images/ubuntu

# Download an artifact
magpie get images/ubuntu:latest

# List versions
magpie ls images/ubuntu

# Create a tag
magpie tag images/ubuntu:latest --as stable
```

## Documentation

- [User Guide](docs/user-guide.md) - Complete CLI and API reference
- [Backup and Restore Guide](docs/backup-restore.md) - Backup procedures and disaster recovery
- [Design Document](docs/design.md) - Architecture and implementation details

## Development

```bash
# Install dependencies
uv sync

# Run server locally
uv run uvicorn magpie.server.app:app --reload

# Run CLI
uv run magpie --help
uv run magpie-ctl --help

# Run with Docker Compose (Caddy + Magpie)
docker compose up --build
```

## Production Deployment

For production with TLS/HTTPS:

```bash
# Set required environment variables
export MAGPIE_DOMAIN=magpie.example.com
export MAGPIE_DATA_DIR=/path/to/persistent/storage

# Start with production configuration
docker compose -f docker-compose.prod.yml up -d
```

**Port conflicts**: The production configuration binds to ports 80 and 443. If other
services (e.g., nginx, Apache, another Caddy instance) are using these ports, either
stop them first or customize the port bindings in `docker-compose.prod.yml`.

The production configuration (`docker-compose.prod.yml` and `Caddyfile.prod`) includes:
- Automatic TLS via Let's Encrypt (or manual certificate configuration)
- Security headers (HSTS, X-Frame-Options, CSP, etc.)
- JSON access logging
- Optional Authentik SSO integration for browser access (see [docs/authentik-setup.md](docs/authentik-setup.md))
- Rate limiting must be configured at the infrastructure layer. See [issue #129](https://github.com/SouthwestCCDC/magpie/issues/129) for implementation options (custom Caddy build, FastAPI middleware, or load balancer).

For manual TLS certificates, edit `Caddyfile.prod` and uncomment the `tls` directive
with your certificate paths.

**Let's Encrypt rate limits**: Let's Encrypt enforces a limit of 50 certificates per
registered domain per week. For testing, use `tls internal` to generate self-signed
certificates, or configure the Let's Encrypt staging environment in `Caddyfile.prod`:
```
tls {
    ca https://acme-staging-v02.api.letsencrypt.org/directory
}
```
The `caddy_data` volume stores issued certificates. Persist this volume across
container recreations to avoid requesting duplicate certificates.

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
| `config` | Show resolved configuration |

### Server Admin (`magpie-ctl`)

| Command | Description |
|---------|-------------|
| `init` | Initialize storage and create admin token |
| `token` | Manage tokens (create, list, revoke) |
| `gc` | Run garbage collection |
| `flush-tag` | Remove tag globally |

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
   git commit -m "Release v1.0.0"
   ```
3. Create and push the tag:
   ```bash
   git tag v1.0.0
   git push origin default --tags
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
| `v1.0.0` | `1.0.0`, `1.0`, `1`, `latest` |
| `v1.0.1` | `1.0.1`, `1.0`, `1`, `latest` |
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
uv pip install git+https://github.com/SouthwestCCDC/magpie@v1.0.0
```

---
*Release documentation generated with AI assistance (Claude Code w/ Opus 4.5).*
