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

- [User Guide](docs/user-guide.md) - CLI and API reference
- [Backup and Restore Guide](docs/backup-restore.md) - Backup and disaster recovery
- [Design Document](docs/design.md) - Architecture details

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

The production configuration includes automatic TLS via Let's Encrypt, security headers, and JSON access logging. For Authentik SSO integration, see [docs/authentik-setup.md](docs/authentik-setup.md).

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

## Installation

```bash
# Server: Use container from registry
docker pull ghcr.io/southwestccdc/magpie:latest

# Client: Install from git tag
uv pip install git+https://github.com/SouthwestCCDC/magpie@v0.1.0
```
