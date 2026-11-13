# Magpie User Guide

Content-addressed artifact storage with mutable tags for distributing build artifacts and binary assets.

## Quick Start

**Key Concepts:**
- **Blob**: Artifact file stored by SHA-256 hash (immutable)
- **Tag**: Human-readable name (mutable, e.g., `latest`, `v1.0`)
- **Hash Ref**: Short reference to blob: `@abc12345` (first 8 chars)

**Authentication:**
- Bearer tokens (required for CLI and API access)
- Optional IP allow-listing lets trusted networks read without a token (see
  [Configuration Reference](configuration.md#access-control-consumed-by-the-bundled-caddy))

New to Magpie? Start with the [5-Minute Quick Start](../README.md#5-minute-quick-start), then
[Installation](installation.md) and [Architecture](architecture.md). For Ansible, see
[ansible-integration.md](ansible-integration.md).

## Client Setup

### Installation

```bash
# With uv (recommended) -- installs `magpie` and `magpie-ctl` into ~/.local/bin
uv tool install git+https://github.com/SouthwestCCDC/magpie

# Or pin a release tag -- see https://github.com/SouthwestCCDC/magpie/releases
uv tool install 'git+https://github.com/SouthwestCCDC/magpie@vX.Y.Z'   # e.g. @v0.2.0
```

Install the CLI at the **same minor version as the server**: an older client is rejected with
`426 Upgrade Required`. See [API Compatibility](api-compatibility.md#version-coupling).

### Configuration

Create `~/.magpie/config.toml`:
```toml
[client]
server = "https://magpie.example.com"
token = "mgp_your_token_here"
```

**Precedence:** CLI flags > environment variables > config file

### Environment Variables

Every Magpie environment variable -- client and server -- is documented in one place:
**[Configuration Reference](configuration.md)**, which also has a "what do I actually need?"
table. The short version for a client:

| Variable | Default | Description |
|----------|---------|-------------|
| `MAGPIE_SERVER` | *(none)* | Server URL (e.g., `https://magpie.example.com`) |
| `MAGPIE_TOKEN` | *(none)* | Bearer token (e.g., `mgp_your_token_here`) |
| `MAGPIE_TIMEOUT` | `600` | Request timeout in seconds or duration format (`30s`, `5m`, `1h30m`). Deliberately not readable from `config.toml`. |
| `MAGPIE_CA_CERT` | *(none)* | Path to an additional CA certificate for HTTPS verification |

Server-side variables (storage, retention, admin-token delivery, logging, observability, IP
allow-listing, S3 backup) live in the [Configuration
Reference](configuration.md#server-variables); [`.env.example`](../.env.example) is the
commented template to copy into a real `.env`.

Comma-separated CIDR ranges are the documented form; whitespace or mixed separators are also
accepted (e.g., `10.0.0.0/8,192.168.1.0/24`).

#### Managing Configuration

Use the `magpie config` command to view or modify configuration without manually editing files:

```bash
magpie config --show                                # View resolved configuration
magpie config --server URL --token TOKEN            # Set server and token
magpie config --clear                               # Reset configuration to defaults
```

Configuration precedence applies: config file < environment variables < CLI flags. `--show`
displays the effective value of each setting and which source it came from, whether or not a
config file exists.

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
| 1 | Operational error (unreadable storage, invalid `--path`, corrupt metadata sidecar, orphan sidecar, or untagged blob without a metadata sidecar) |
| 3 | Tagged blobs or their metadata sidecars are missing |
| 5 | Content mismatch: stored bytes do not match the recorded SHA-256 |

`Missing blob` is reported only for blobs a tag still points at, since those are the ones GC never collects. Untagged blobs with a metadata sidecar are reported as orphan-sidecar errors (exit `1`); untagged blobs without a sidecar are also errors (exit `1`), not data loss—re-run the scrub if one persists past GC retention, since GC should remove it.

Avoid running a scrub while GC is deleting blobs: GC unlinks a blob before its metadata sidecar, so an overlapping scrub sees records in flux. The shipped cron/systemd units take the GC lock to prevent this, waiting up to an hour for it and reporting a skipped run as `75` (a `flock` status, never produced by `verify` itself) so it is not mistaken for a storage problem.

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

*(AI-generated via Claude Code w/ Sonnet 4.5; refreshed for v0.2.0 final via Devin; multi-CIDR separators clarified for #612 via Devin; untagged metadata-less blobs exit 1, via Devin)*
