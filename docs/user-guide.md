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
# With uv (recommended)
uv pip install git+https://github.com/SouthwestCCDC/magpie.git@v0.1.0
```

### Configuration

Create `~/.magpie/config.toml`:
```toml
[client]
server = "https://magpie.example.com"
token = "mgp_your_token_here"
```

**Precedence:** CLI flags > environment variables > config file

| Env Var | Use |
|---------|-----|
| `MAGPIE_SERVER` | Server URL |
| `MAGPIE_TOKEN` | Bearer token |
| `MAGPIE_TIMEOUT` | Request timeout (default: 600s) |
| `MAGPIE_CA_CERT` | Custom CA certificate path |

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

**Server status:**
```bash
magpie status              # Check connectivity and health
magpie url images/ubuntu   # Get download URL for scripting
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
- **No built-in rate limiting** - Deploy behind Cloudflare or use Caddy's rate_limit plugin
- **Tokens don't expire** - Rotate tokens quarterly: `magpie-ctl token revoke old-token` + `token create`

---

*(AI-generated via Claude Code w/ Opus 4.5)*
