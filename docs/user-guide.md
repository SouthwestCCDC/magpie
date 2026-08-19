# Magpie User Guide

Content-addressed artifact storage with mutable tags for distributing build artifacts and binary assets.

## Quick Start

**Key Concepts:**
- **Blob**: Artifact file stored by SHA-256 hash (immutable)
- **Tag**: Human-readable name (mutable, e.g., `latest`, `v1.0`)
- **Hash Ref**: Short reference to blob: `@abc12345` (first 8 chars)

**Authentication:**
- Bearer tokens (required for CLI and API access)
- Authentik SSO for browser access (optional)

For Authentik setup, see [authentik-setup.md](authentik-setup.md). For Ansible, see [ansible-integration.md](ansible-integration.md).

## Client Setup

### Installation

```bash
# With uv (recommended) - replace <VERSION> with the latest release tag (e.g., v0.1.3)
# See https://github.com/SouthwestCCDC/magpie/releases
uv pip install git+https://github.com/SouthwestCCDC/magpie.git@v<VERSION>
```

### Configuration

Create `~/.magpie/config.toml`:
```toml
[client]
server = "https://magpie.example.com"
token = "mgp_your_token_here"
```

**Precedence:** CLI flags > environment variables > config file

### Client Environment Variables

All client environment variables can be overridden by CLI flags. Server and token can also be set in `~/.magpie/config.toml`.

| Variable | Default | Description |
|----------|---------|-------------|
| `MAGPIE_SERVER` | *(none)* | Server URL (e.g., `https://magpie.example.com`) |
| `MAGPIE_TOKEN` | *(none)* | Bearer token (e.g., `mgp_your_token_here`) |
| `MAGPIE_TIMEOUT` | `600` | Request timeout in seconds or duration format (`30s`, `5m`, `1h`, `1h30m`). Not configurable in `config.toml` to prevent silent network behavior changes. |
| `MAGPIE_CA_CERT` | *(none)* | Path to custom CA certificate for TLS verification |

### Server Environment Variables

Server configuration uses environment variables with the `MAGPIE_` prefix. See `.env.example` for complete examples.

| Variable | Default | Description |
|----------|---------|-------------|
| **Storage** | | |
| `MAGPIE_STORAGE_PATH` | `/data/artifacts` | Root directory for artifact storage |
| `MAGPIE_TEMP_PATH` | `{storage}/.tmp` | Temporary files during uploads (derived from storage path) |
| `MAGPIE_DATABASE_PATH` | `/data/magpie.db` | SQLite database path for token storage |
| `MAGPIE_RETENTION_DAYS` | `90` | Days before untagged blobs are eligible for GC |
| `MAGPIE_MAX_UPLOAD_SIZE` | *(none)* | Maximum upload size in bytes (leave empty for unlimited) |
| **S3 Backup** | | |
| `MAGPIE_S3_BUCKET` | *(none)* | S3 bucket name for artifact backup (leave empty to disable) |
| `MAGPIE_S3_PREFIX` | *(empty)* | Optional S3 key prefix (e.g., `magpie/backups`) |
| `AWS_ACCESS_KEY_ID` | *(none)* | AWS credentials for S3 backup (or use IAM role) |
| `AWS_SECRET_ACCESS_KEY` | *(none)* | AWS secret key for S3 backup |
| `AWS_SESSION_TOKEN` | *(none)* | AWS session token for temporary credentials (STS) |
| `AWS_DEFAULT_REGION` | *(none)* | AWS region for S3 bucket |
| **Logging & Debug** | | |
| `MAGPIE_LOG_FORMAT` | `json` | Log format: `json` for structured logs, `console` for human-readable |
| `MAGPIE_DEBUG` | `false` | Enable debug mode (verbose logging, detailed error responses) |
| **Observability** | | |
| `MAGPIE_SENTRY_DSN` | *(none)* | Sentry DSN for error tracking (leave empty to disable) |
| `MAGPIE_OTEL_ENABLED` | `false` | Enable OpenTelemetry tracing |
| `MAGPIE_OTEL_ENDPOINT` | *(none)* | OpenTelemetry collector endpoint (required if OTEL enabled) |
| `MAGPIE_OTEL_SERVICE_NAME` | `magpie` | Service name for OpenTelemetry traces |
| **Security** | | |
| `MAGPIE_ALLOWED_CIDRS` | *(empty)* | Comma-separated CIDR ranges for read-only IP allow-listing (e.g., `10.0.0.0/8,192.168.1.0/24`). Requests from these IPs can read artifacts without bearer tokens. Write operations still require tokens. |
| `MAGPIE_TRUSTED_PROXIES` | *(empty)* | Space-separated IPs/CIDRs whose `X-Forwarded-For` header Caddy trusts when determining the client IP used by `MAGPIE_ALLOWED_CIDRS` above. Default trusts no proxy (real connecting peer's IP is used). Only set this to the exact upstream hop(s) if Caddy sits behind another reverse proxy -- never a broad range, which would let clients on it spoof their source IP. See [installation.md](installation.md#trusted-proxies). |
| `AUTHENTIK_HOST` | *(none)* | Authentik server hostname for SSO browser access to `/artifacts/*` (e.g., `authentik.example.com`). API access via bearer tokens continues to work. See `docs/authentik-setup.md`. |
| **Docker Compose Only** | | |
| `MAGPIE_DATA_DIR` | `./data` | Host directory for data storage (mounted at `/data` in containers). Only used by `docker-compose.yml`, not the application. |
| `MAGPIE_HTTP_PORT` | `8080` | External HTTP port for Caddy. Docker Compose only. |
| `MAGPIE_HTTPS_PORT` | `8443` | External HTTPS port for Caddy. Docker Compose only. |
| `MAGPIE_UID` | *(auto)* | User ID for magpie process (auto-detected from volume ownership). Used by `entrypoint.sh`, not the application. |
| `MAGPIE_GID` | *(auto)* | Group ID for magpie process (auto-detected from volume ownership). Used by `entrypoint.sh`, not the application. |

#### Managing Configuration

Use the `magpie config` command to view or modify configuration without manually editing files:

```bash
magpie config --show                                # View resolved configuration
magpie config --server URL --token TOKEN            # Set server and token
magpie config --clear                               # Reset configuration to defaults
```

Configuration precedence applies: config file < environment variables < CLI flags. The `--show` command displays the effective configuration after applying all sources.

### Token Scopes

| Scope | Permissions |
|-------|------------|
| `read` | List and download artifacts |
| `write` | + upload and manage tags |
| `admin` | + manage tokens and GC |

## Basic Usage

**Upload:**
```bash
magpie push myfile.tar.gz --to images/ubuntu          # Basic
magpie push build.zip --to builds/app --source-uri git://repo@v1.0  # With metadata
magpie push config.tar.gz --to configs/nginx --no-latest  # Without auto-tagging
```

**List versions:**
```bash
magpie ls images/ubuntu
```

**Download:**
```bash
magpie get images/ubuntu                 # Latest
magpie get images/ubuntu:stable          # By tag
magpie get images/ubuntu:@a1b2c3d4       # By hash
magpie get images/ubuntu:latest -o file  # Custom path
magpie get images/ubuntu --force         # Force re-download
```

**Manage tags:**
```bash
magpie tag images/ubuntu:latest --as stable   # Create tag
magpie untag images/ubuntu v1.0               # Remove tag
magpie flush-tag deprecated --dry-run         # Remove globally (preview)
magpie info images/ubuntu:@a1b2c3d4           # View metadata
magpie amend images/ubuntu:latest --source-uri https://github.com/example/repo  # Update metadata
```

**Server status and version:**
```bash
magpie status                    # Check server status/health (requires admin token)
magpie version                   # Show client version
magpie version --server-version  # Check connectivity and show both client and server versions
magpie url images/ubuntu         # Get download URL for scripting
```

## Server Initialization

See [installation.md](installation.md) for deployment. For token management:

```bash
# First initialization (auto-runs on container startup)
docker compose exec magpie magpie-ctl init

# Create tokens
docker compose exec magpie magpie-ctl token create --name ci-reader --scope read
docker compose exec magpie magpie-ctl token list
docker compose exec magpie magpie-ctl token revoke ci-reader
```

**Configuration:** See [installation.md](installation.md) for environment variables and TLS setup.

## Operations Guide

**Tagging strategy:**
- `latest` - Most recent (auto-created)
- `stable` - Production-ready (manually promoted)
- `v1.0`, `v1.1` - Semantic versions
- Use tags for production artifacts

**Garbage collection:**
```bash
magpie gc --dry-run                        # Preview
magpie gc                                  # Run (requires admin token)
docker compose exec magpie magpie-ctl gc   # Server-side
```

Untagged blobs older than retention period (default 90 days) are eligible for removal. See [backup-restore.md](backup-restore.md) for full operations guide.

**Integrity verification (scrub):**

`magpie-ctl verify` re-reads stored blobs and compares them against the SHA-256 recorded in their metadata at upload time, catching bit-rot, truncation, and tampering of artifacts such as OpenVPN CA/cert/key material. It is server-side only (it reads the storage filesystem directly); there is no client-side equivalent.

```bash
docker compose exec magpie magpie-ctl verify                       # Full scrub
docker compose exec magpie magpie-ctl verify --path openvpn        # Scope to a path prefix
docker compose exec magpie magpie-ctl verify --limit 500 --quiet   # Bound a partial scrub
docker compose exec magpie magpie-ctl --format json verify         # Structured output
```

A full scrub re-reads every byte in storage, so bound scheduled runs with `--path`, `--limit` (blobs), or `--max-bytes`. Blobs are hashed in 1MB chunks and never loaded into memory. `--max-issues` (default 1000) caps how many individual findings are listed; the summary counters always reflect every issue found.

Exit codes are suitable for cron/monitoring:

| Code | Meaning |
| ---- | ------- |
| 0 | Every verified blob matched its recorded hash |
| 1 | Operational error (unreadable storage, invalid `--path`, corrupt metadata sidecar, orphan sidecar) |
| 3 | A tagged blob or a metadata sidecar is missing |
| 5 | Content mismatch: stored bytes do not match the recorded SHA-256 |

`Missing blob` is reported only for blobs a tag still points at, since those are the ones GC never collects; a blob recorded only by a leftover metadata sidecar is reported as an orphan sidecar under `Errors` (exit `1`), meaning storage bookkeeping to clean up rather than lost content.

Avoid running a scrub while GC is deleting blobs: GC unlinks a blob before its metadata sidecar, so an overlapping scrub sees records in flux. The shipped cron/systemd units take the GC lock to prevent this.

Exit code 5 means a competition-critical artifact may be damaged or tampered with: restore that artifact from backup (see [backup-restore.md](backup-restore.md)) rather than re-uploading over it. See [monitoring.md](monitoring.md) for alerting and [../deployment/README.md](../deployment/README.md) for scheduling a periodic scrub.

**S3 backup/restore:**
```bash
docker compose exec magpie magpie-ctl sync to-s3 --dry-run  # Preview backup
docker compose exec magpie magpie-ctl sync to-s3             # Backup to S3
docker compose exec magpie magpie-ctl sync from-s3           # Restore from S3
```

Requires `MAGPIE_S3_BUCKET` and AWS credentials.

**Troubleshooting:**
- `401 Unauthorized` - Invalid/missing token
- `403 Forbidden` - Insufficient permissions
- `404 Not Found` - Artifact doesn't exist
- `"Duplicate" on upload` - Content already exists (normal)

## Security Notes

- **Token scopes are global** - Cannot restrict tokens to specific paths; use separate instances for strict isolation
- **IP allow-list is global** - `MAGPIE_ALLOWED_CIDRS` bypasses auth for entire server
- **Trusted proxies must be scoped tightly** - `MAGPIE_TRUSTED_PROXIES` controls which upstream hop(s) Caddy trusts to set `X-Forwarded-For`; a broad range lets any client on it spoof its source IP and bypass `MAGPIE_ALLOWED_CIDRS`
- **No built-in rate limiting** - Deploy behind Cloudflare or use Caddy's rate_limit plugin
- **Tokens don't expire** - Rotate tokens quarterly: `magpie-ctl token revoke old-token` + `token create`

---

*(AI-generated via Claude Code w/ Sonnet 4.5)*
