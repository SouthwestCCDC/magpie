# Magpie User Guide

Magpie is a content-addressed artifact storage system with mutable tags,
designed for distributing build artifacts, container images, and other
binary assets across infrastructure.

## Overview

Magpie stores binary artifacts by content hash with mutable tags for versioning. Features: deduplication, garbage collection, optional provenance tracking.

### Key Concepts

| Concept | Description |
|---------|-------------|
| **Blob** | The actual artifact file, stored by its SHA-256 hash. Immutable. |
| **Tag** | A human-readable name pointing to a specific blob (e.g., `latest`, `v1.0`). Mutable. |
| **Hash Ref** | Short reference to a blob: `@` + first 8 hex chars of hash (e.g., `@abc12345`). |
| **Artifact Path** | Logical namespace for artifacts (e.g., `images/ubuntu`, `builds/app`). |
| **Retention** | Untagged blobs older than retention period are eligible for garbage collection. |


### Authentication Methods

Magpie supports two authentication methods:

1. **Bearer Tokens** (for CLI, API, and automation)
   - Scoped tokens: read, write, or admin
   - Created via `magpie-ctl token create`
   - Used in `Authorization: Bearer <token>` header
   - Required for CLI operations and API access

2. **Authentik SSO** (for browser access, optional)
   - Web-based browsing of `/artifacts/*` paths
   - Seamless SSO integration for team members
   - Configured server-side (see [docs/authentik-setup.md](authentik-setup.md))
   - Does not affect CLI or API access

This guide focuses on CLI usage with bearer tokens. For Authentik SSO setup,
see the [Authentik Integration Guide](authentik-setup.md). For using Magpie
with Ansible playbooks, see the [Ansible Integration Guide](ansible-integration.md).

## Client Setup

### Installation

Install the magpie CLI using pip or uv:

```bash
# With uv (recommended)
uv pip install git+https://github.com/SouthwestCCDC/magpie.git@v0.1.0

# With pip
pip install git+https://github.com/SouthwestCCDC/magpie.git@v0.1.0

# For development (from local clone)
cd magpie
uv pip install -e .
```

This installs `magpie` (client) and `magpie-ctl` (admin) CLI tools.

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
| `MAGPIE_TIMEOUT` | Request timeout (see note below) | `600`, `5m`, `1h30m` |
| `MAGPIE_CA_CERT` | Additional CA certificate path for HTTPS | `/etc/ssl/certs/internal-ca.crt` |

### Configuration Precedence

1. CLI flags (`--server`, `--token`) - highest priority
2. Environment variables (`MAGPIE_SERVER`, `MAGPIE_TOKEN`)
3. Config file (`~/.magpie/config.toml`)
4. Defaults - lowest priority

**Note:** The `--timeout` setting has a different precedence. It can only be set via
CLI flag or the `MAGPIE_TIMEOUT` environment variable -- it is intentionally not read
from the config file. This prevents long-lived global configuration from silently
affecting network behavior. The default is 600 seconds (10 minutes).

### SSL/TLS Configuration

For internal CAs or self-signed certificates:

```bash
magpie --ca-cert /etc/ssl/certs/internal-ca.crt ls images/
# or
export MAGPIE_CA_CERT=/etc/ssl/certs/internal-ca.crt
```

### Managing Configuration

```bash
magpie config --server https://magpie.example.com --token mgp_abc123
magpie config --show
magpie config --clear
```

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
Download: https://magpie.example.com/artifacts/images/ubuntu/latest
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

# Force overwrite existing file and re-download even if local hash matches
magpie get images/ubuntu:latest --force
```

Options:

| Option | Description |
|--------|-------------|
| `-o`, `--output PATH` | Output file path (default: derived from artifact path) |
| `--no-verify` | Skip SHA-256 hash verification |
| `-q`, `--quiet` | Suppress progress output |
| `-f`, `--force` | Overwrite existing file without prompting, re-download even if local hash matches |

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

### Flush a Tag Globally

Remove a tag from all artifacts:

```bash
magpie flush-tag old-release --dry-run    # Preview
magpie flush-tag deprecated               # Confirm prompt
magpie flush-tag latest --force --yes      # Protected tags
```

Requires admin scope.

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

### Check Server Status

```bash
# Check server connectivity and health
magpie status
```

Output:
```
Server:    https://magpie.example.com
Status:    OK
Version:   0.1.0
Auth:      Token valid (admin scope, name: ci-bot)
Storage:   1.2 GB used
Artifacts: 42 total
Blobs:     156 total
```

Use this to verify server connectivity and authentication before operations.

## Server Setup

Deploy using Docker Compose:

```bash
docker compose up -d
```

Customize ports via environment variables:

```bash
MAGPIE_HTTP_PORT=80 MAGPIE_HTTPS_PORT=443 docker compose up -d
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

#### Custom Admin Token

```bash
docker compose exec magpie magpie-ctl init --admin-token mgp_ADMIN_your_token_here
```

Token must start with `mgp_ADMIN_` and use cryptographically secure generation (e.g., `openssl rand -base64 32`). Never commit tokens to version control.

To regenerate a compromised token:

```bash
docker compose exec magpie magpie-ctl init --reset-admin-token
```

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
| `MAGPIE_TEMP_PATH` | `/data/artifacts/.tmp` | Temporary upload directory (defaults under `MAGPIE_STORAGE_PATH`, derived if storage path is overridden) |
| `MAGPIE_DATABASE_PATH` | `/data/artifacts/.magpie.db` | SQLite token database path (defaults under `MAGPIE_STORAGE_PATH`, derived if storage path is overridden) |
| `MAGPIE_RETENTION_DAYS` | `90` | Days before untagged blobs can be GC'd |
| `MAGPIE_DEBUG` | `false` | Enable debug logging |
| `MAGPIE_MAX_UPLOAD_SIZE` | (none) | Max upload size in bytes (none = unlimited) |
| `MAGPIE_S3_BUCKET` | (none) | S3 bucket name for backups (required for sync commands) |
| `MAGPIE_S3_PREFIX` | `""` | Optional prefix for S3 keys |
| `MAGPIE_LOG_FORMAT` | `json` | Log format: `json` or `console` |
| `MAGPIE_SENTRY_DSN` | (none) | Sentry DSN for error tracking |
| `MAGPIE_OTEL_ENABLED` | `false` | Enable OpenTelemetry tracing |
| `MAGPIE_OTEL_ENDPOINT` | (none) | OTEL collector endpoint (required when `MAGPIE_OTEL_ENABLED=true`) |
| `MAGPIE_OTEL_SERVICE_NAME` | `magpie` | Service name for OTEL traces |
| `MAGPIE_ALLOWED_CIDRS` | `""` | Comma-separated CIDR ranges for IP-based auth bypass (consumed by Caddy) |

### TLS Configuration

**Caddy Environment Variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `MAGPIE_DOMAIN` | (none) | Domain name for Caddy TLS (required in production with Caddyfile.prod, consumed by Caddy not Python server) |

For production, configure Caddy for automatic TLS in `Caddyfile.prod`:

```caddyfile
# Caddyfile.prod
{$MAGPIE_DOMAIN} {
    # Routes configured automatically
    import /etc/caddy/magpie-routes
}
```

Caddy automatically obtains and renews Let's Encrypt certificates.

## Operations Guide

### Tagging Strategies

- `latest`: Most recent build (auto-created)
- `stable`: Production-ready (manually promoted)
- `v1.0`, `v1.1`: Semantic versions for releases
- `game-quals`, `game-finals`: Event-pinned for competition
- Always tag production with semantic versions

### Retention and Garbage Collection

Untagged blobs are automatically eligible for cleanup after the retention period
(default: 90 days). Tagged blobs are never deleted by GC.

**Preview what would be deleted (via client):**

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

**Run garbage collection (via client):**

```bash
magpie gc
```

**Note**: GC requires an admin token.

**Server-side GC (magpie-ctl):**

```bash
docker compose exec magpie magpie-ctl gc --dry-run              # Preview
docker compose exec magpie magpie-ctl gc                        # Run GC
docker compose exec magpie magpie-ctl gc --retention-days 7     # Override retention
```


### Troubleshooting

- **"No server configured"**: `export MAGPIE_SERVER=https://magpie.example.com`
- **"401 Unauthorized"**: Token missing or invalid. Check `MAGPIE_TOKEN` or config file.
- **"403 Forbidden"**: Token lacks permissions. Contact administrator.
- **"Hash mismatch"**: Network or storage corruption. Try downloading again.
- **"Duplicate" on upload**: Normal. Content-addressed deduplication returned existing hash.

## Quick Reference

**Commands:**

- `magpie status` - Check server connectivity and health
- `magpie push FILE --to PATH` - Upload
- `magpie get PATH:REF` - Download
- `magpie ls PATH` - List versions
- `magpie info PATH:REF` - Metadata
- `magpie tag PATH:REF --as TAG` - Tag
- `magpie untag PATH TAG` - Remove tag
- `magpie gc [--dry-run]` - Garbage collection

**Reference formats:**

- `images/ubuntu` - Latest version
- `images/ubuntu:stable` - Specific tag
- `images/ubuntu:@a1b2c3d4` - Hash ref (immutable)

### API Endpoints

**Public (no auth):**

- `GET /health` - Health check
- `GET /api/v1/auth/validate` - Token validation (forward auth)
- `GET /artifacts/public/*` - Public downloads

**Protected (Bearer token required):**

- `GET /api/v1/artifacts` - List artifacts (read)
- `GET /api/v1/artifacts/{path}` - List versions (read)
- `GET /artifacts/{path}/{tag}` - Download by tag (read)
- `GET /artifacts/{path}/blobs/{hash}` - Download by hash (read)
- `POST /api/v1/upload/{path}` - Upload (write)
- `POST /api/v1/artifacts/{path}/{ref}/tags` - Create tag (write)
- `DELETE /api/v1/artifacts/{path}/tags/{tag}` - Remove tag (write)
- `POST /api/v1/gc` - Run GC (admin)
- `POST /api/v1/tokens` - Create token (admin)

### curl Examples

```bash
# Health check
curl https://magpie.example.com/health

# Upload (requires token)
curl -X POST \
  -H "Authorization: Bearer mgp_token" \
  -F "file=@myfile.tar.gz" \
  https://magpie.example.com/api/v1/upload/images/ubuntu

# Download by tag (requires Bearer token)
curl -H "Authorization: Bearer $MAGPIE_TOKEN" \
  -O https://magpie.example.com/artifacts/images/ubuntu/latest

# Download by hash (requires Bearer token)
curl -H "Authorization: Bearer $MAGPIE_TOKEN" \
  -O https://magpie.example.com/artifacts/images/ubuntu/blobs/a1b2c3d4
```
