# Magpie User Guide

Magpie is a content-addressed artifact storage system with mutable tags,
designed for distributing build artifacts, container images, and other
binary assets across infrastructure.

## Overview

### What Magpie Solves

Magpie provides a simple, reliable way to:

- Store and retrieve binary artifacts with content-based deduplication
- Version artifacts using human-readable tags (like `latest`, `stable`, `v1.0`)
- Track artifact provenance with optional source URI metadata
- Automatically clean up old, untagged artifacts via garbage collection
- Distribute artifacts across your infrastructure with minimal bandwidth

### Key Concepts

| Concept | Description |
|---------|-------------|
| **Blob** | The actual artifact file, stored by its SHA-256 hash. Immutable. |
| **Tag** | A human-readable name pointing to a specific blob (e.g., `latest`, `v1.0`). Mutable. |
| **Hash Ref** | Short reference to a blob: `@` + first 8 hex chars of hash (e.g., `@abc12345`). |
| **Artifact Path** | Logical namespace for artifacts (e.g., `images/ubuntu`, `builds/app`). |
| **Retention** | Untagged blobs older than retention period are eligible for garbage collection. |

### Use Cases

- **SWCCDC image distribution**: Push VM images and retrieve them by tag
- **Build artifacts**: Store CI/CD outputs with commit-based tags
- **Configuration bundles**: Versioned config packages for deployment
- **Firmware distribution**: Immutable firmware blobs with version tags

## Client Setup

### Installation

Install from source:

```bash
cd magpie
pip install .
```

This installs two CLI tools:
- `magpie` - Client for interacting with the server
- `magpie-ctl` - Server administration tool

### Configuration File

Create `~/.magpie/config.toml`:

```toml
[client]
server = "https://magpie.example.com"
token = "mgp_your_token_here"
```

### Environment Variables

Environment variables override config file values:

| Variable | Description | Example |
|----------|-------------|---------|
| `MAGPIE_SERVER` | Server URL | `https://magpie.example.com` |
| `MAGPIE_TOKEN` | Authentication token | `mgp_abc123...` |

### Configuration Precedence

1. CLI flags (`--server`, `--token`) - highest priority
2. Environment variables (`MAGPIE_SERVER`, `MAGPIE_TOKEN`)
3. Config file (`~/.magpie/config.toml`)
4. Defaults - lowest priority

### Getting a Token

Contact your Magpie administrator to obtain a token. Tokens have scopes:

| Scope | Capabilities |
|-------|--------------|
| `read` | List artifacts, download files, view metadata |
| `write` | All read permissions + upload, tag, untag |
| `admin` | All write permissions + manage tokens, run GC |

## Basic Usage

### Upload an Artifact

```bash
# Upload a file to an artifact path
magpie push myfile.tar.gz --to images/ubuntu

# Upload with source URI for provenance tracking
magpie push build.zip --to builds/app --source-uri git://repo@v1.0

# Upload without auto-tagging as "latest"
magpie push config.tar.gz --to configs/nginx --no-latest
```

Output:
```
Uploaded: a1b2c3d4e5f6...
Hash ref: @a1b2c3d4
Download: https://magpie.example.com/artifacts/images/ubuntu/@a1b2c3d4
```

### List Artifact Versions

```bash
# List all versions of an artifact
magpie ls images/ubuntu
```

Output:
```
HASH         TAGS                 UPLOADED_BY     UPLOADED_AT
@a1b2c3d4    latest, v2.0         ci-bot          2024-01-15 10:30:00
@e5f6g7h8    v1.0, stable         ci-bot          2024-01-10 09:15:00
```

### Download an Artifact

```bash
# Download latest version (default)
magpie get images/ubuntu

# Download by tag
magpie get images/ubuntu:stable

# Download by hash ref
magpie get images/ubuntu:@a1b2c3d4

# Download to specific path
magpie get images/ubuntu:latest -o ubuntu-latest.tar.gz

# Skip hash verification (not recommended)
magpie get images/ubuntu --no-verify
```

### View Artifact Metadata

```bash
# Show metadata for latest
magpie info images/ubuntu

# Show metadata for specific version
magpie info images/ubuntu:@a1b2c3d4
```

Output:
```
Hash:        a1b2c3d4e5f6g7h8...
Hash Ref:    @a1b2c3d4
Uploaded By: ci-bot
Uploaded At: 2024-01-15 10:30:00
Source URI:  git://repo@v2.0
Tags:        latest, v2.0
```

### Create Tags

```bash
# Tag current latest as stable
magpie tag images/ubuntu:latest --as stable

# Tag specific version
magpie tag images/ubuntu:@a1b2c3d4 --as v2.0

# Update existing tag (moves it to new target)
magpie tag images/ubuntu:@e5f6g7h8 --as stable
```

### Remove Tags

```bash
# Remove a tag from an artifact
magpie untag images/ubuntu v1.0
```

### Get Download URL

```bash
# Get URL for scripting with curl/wget
magpie url images/ubuntu:latest

# Use in scripts
curl -O $(magpie url images/ubuntu:latest)
wget $(magpie url builds/app:v1.0)
```

### Update Metadata

```bash
# Add or update source URI
magpie amend images/ubuntu:latest --source-uri https://github.com/example/repo

# Clear source URI
magpie amend images/ubuntu:latest --source-uri ""
```

## Server Setup

### Docker Compose Deployment

The recommended deployment uses Docker Compose with Caddy as reverse proxy:

```bash
# Clone repository
git clone https://github.com/your-org/magpie.git
cd magpie

# Start services
docker compose up -d
```

Services:
- **Caddy** (port 8080 by default): Reverse proxy, TLS termination, static file serving
- **Magpie** (internal): FastAPI backend for API operations

### Port Configuration

The HTTP and HTTPS ports can be customized via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `MAGPIE_HTTP_PORT` | `8080` | External HTTP port |
| `MAGPIE_HTTPS_PORT` | `8443` | External HTTPS port |

Example with custom ports:

```bash
MAGPIE_HTTP_PORT=80 MAGPIE_HTTPS_PORT=443 docker compose up -d
```

Or create a `.env` file in the same directory as `docker-compose.yml`:

```bash
MAGPIE_HTTP_PORT=80
MAGPIE_HTTPS_PORT=443
```

### First-Time Initialization

```bash
# Initialize storage and generate admin token
docker compose exec magpie magpie-ctl init
```

Output:
```
Storage initialized at: /data/artifacts
Database initialized at: /data/artifacts/.magpie.db

============================================================
ADMIN TOKEN (store securely, only shown once!):
mgp_abc123def456...
============================================================
```

**Important**: Save this token securely. It cannot be recovered.

### Creating Additional Tokens

```bash
# Create a read-only token
docker compose exec magpie magpie-ctl token create --name ci-reader --scope read

# Create a write token
docker compose exec magpie magpie-ctl token create --name deployer --scope write

# Create an admin token
docker compose exec magpie magpie-ctl token create --name ops-admin --scope admin

# List all tokens
docker compose exec magpie magpie-ctl token list

# Revoke a token
docker compose exec magpie magpie-ctl token revoke ci-reader
```

### Server Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MAGPIE_STORAGE_PATH` | `/data/artifacts` | Root directory for artifact storage |
| `MAGPIE_RETENTION_DAYS` | `90` | Days before untagged blobs can be GC'd |
| `MAGPIE_DEBUG` | `false` | Enable debug logging |
| `MAGPIE_SENTRY_DSN` | (none) | Sentry DSN for error tracking |
| `MAGPIE_OTEL_ENABLED` | `false` | Enable OpenTelemetry tracing |

### TLS Configuration

For production, configure Caddy for automatic TLS:

```caddyfile
# Caddyfile
magpie.example.com {
    # Routes configured automatically
    import /etc/caddy/magpie-routes
}
```

Caddy automatically obtains and renews Let's Encrypt certificates.

## Operations Guide

### Tagging Strategies

**Recommended patterns:**

| Tag | Purpose | Example |
|-----|---------|---------|
| `latest` | Most recent successful build | Auto-created on upload |
| `stable` | Production-ready version | Manually promoted |
| `v1.0`, `v1.1` | Semantic versions | For releases |
| `game-quals`, `game-finals` | Event-pinned | For competition |
| `sha-abc1234` | Git commit reference | For traceability |

**Best practices:**

- Always tag production artifacts with semantic versions
- Use `latest` for development/testing
- Create event-specific tags before competitions
- Document tag meanings in your team wiki

### Retention and Garbage Collection

Untagged blobs are automatically eligible for cleanup after the retention period
(default: 90 days). Tagged blobs are never deleted by GC.

**Preview what would be deleted:**

```bash
magpie gc --dry-run
```

Output:
```
GC Preview (dry run):
  Artifacts scanned: 42
  Blobs found: 156
  Would delete: 23 blob(s)
  Space reclaimable: 1.2 GB
```

**Run garbage collection:**

```bash
magpie gc
```

**Note**: GC requires an admin token.

### Troubleshooting

#### "No server configured"

Set the server URL:
```bash
export MAGPIE_SERVER=https://magpie.example.com
# or
magpie --server https://magpie.example.com ls images/ubuntu
```

#### "401 Unauthorized"

Your token is missing or invalid:
```bash
export MAGPIE_TOKEN=mgp_your_token_here
# or create ~/.magpie/config.toml with token
```

#### "403 Forbidden"

Your token lacks required permissions:
- Upload/tag operations require `write` scope
- Token management/GC require `admin` scope

Contact your administrator for a token with appropriate scope.

#### "Hash mismatch" on download

The downloaded file doesn't match expected hash. This could indicate:
- Network corruption during transfer
- Storage corruption on server

Try downloading again. If it persists, contact your administrator.

#### Upload shows "Duplicate"

This is normal behavior. Content-addressed storage deduplicates identical files.
The artifact was already stored; the existing hash was returned.

## Quick Reference

### Command Cheatsheet

| Command | Description |
|---------|-------------|
| `magpie push FILE --to PATH` | Upload artifact |
| `magpie get PATH:REF` | Download artifact |
| `magpie ls PATH` | List versions |
| `magpie info PATH:REF` | Show metadata |
| `magpie url PATH:REF` | Get download URL |
| `magpie tag PATH:REF --as TAG` | Create/update tag |
| `magpie untag PATH TAG` | Remove tag |
| `magpie amend PATH:REF --source-uri URI` | Update metadata |
| `magpie gc [--dry-run]` | Run garbage collection |
| `magpie version` | Show version |

### Reference Formats

| Format | Example | Description |
|--------|---------|-------------|
| `PATH` | `images/ubuntu` | Artifact path only (uses `latest`) |
| `PATH:TAG` | `images/ubuntu:stable` | Path with tag |
| `PATH:@HASH` | `images/ubuntu:@a1b2c3d4` | Path with hash ref |

### Environment Variables

| Variable | Scope | Description |
|----------|-------|-------------|
| `MAGPIE_SERVER` | Client | Server URL |
| `MAGPIE_TOKEN` | Client | Auth token |
| `MAGPIE_HTTP_PORT` | Deploy | External HTTP port (default: 8080) |
| `MAGPIE_HTTPS_PORT` | Deploy | External HTTPS port (default: 8443) |
| `MAGPIE_STORAGE_PATH` | Server | Storage directory |
| `MAGPIE_RETENTION_DAYS` | Server | GC retention period |
| `MAGPIE_DEBUG` | Both | Enable debug mode |

### API Endpoints

**Public (no auth required):**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/api/v1/artifacts/{path}` | List versions |
| GET | `/api/v1/artifacts/{path}/{ref}/info` | Get metadata |
| GET | `/artifacts/{path}/{ref}` | Download file |

**Protected (auth required):**

| Method | Endpoint | Scope | Description |
|--------|----------|-------|-------------|
| POST | `/api/v1/upload/{path}` | write | Upload artifact |
| POST | `/api/v1/artifacts/{path}/{ref}/tags` | write | Create tag |
| DELETE | `/api/v1/artifacts/{path}/tags/{tag}` | write | Remove tag |
| PATCH | `/api/v1/artifacts/{path}/{ref}` | write | Amend metadata |
| GET | `/api/v1/tokens` | admin | List tokens |
| POST | `/api/v1/tokens` | admin | Create token |
| DELETE | `/api/v1/tokens/{name}` | admin | Revoke token |
| POST | `/api/v1/gc` | admin | Run GC |

### curl Examples

```bash
# Health check
curl https://magpie.example.com/health

# List versions
curl https://magpie.example.com/api/v1/artifacts/images/ubuntu

# Get metadata
curl https://magpie.example.com/api/v1/artifacts/images/ubuntu/latest/info

# Download file
curl -O https://magpie.example.com/artifacts/images/ubuntu/@a1b2c3d4

# Upload (requires token)
curl -X POST \
  -H "Authorization: Bearer mgp_your_token" \
  -F "file=@myfile.tar.gz" \
  https://magpie.example.com/api/v1/upload/images/ubuntu

# Create tag (requires token)
curl -X POST \
  -H "Authorization: Bearer mgp_your_token" \
  -H "Content-Type: application/json" \
  -d '{"tag_name": "v1.0"}' \
  https://magpie.example.com/api/v1/artifacts/images/ubuntu/@a1b2c3d4/tags

# Create token (requires admin token)
curl -X POST \
  -H "Authorization: Bearer mgp_admin_token" \
  -H "Content-Type: application/json" \
  -d '{"name": "ci-bot", "scope": "write"}' \
  https://magpie.example.com/api/v1/tokens
```
