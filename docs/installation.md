# Magpie Installation Guide

This guide walks you through installing and configuring Magpie, a content-addressed artifact storage system with mutable tags.

## Prerequisites

Before installing Magpie, ensure you have:

### Required Software

- **Docker** 24.0+ and **Docker Compose** v2.1+
  - For Docker Compose healthcheck support
  - Verify: `docker --version && docker compose version`

- **Python** 3.13+ (for CLI client installation)
  - Verify: `python --version`

### Optional Software

- **uv** (recommended Python package manager)
  - Install: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Alternative: Use `pip` instead

### System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | 2 cores | 4+ cores |
| RAM | 2 GB | 4+ GB |
| Disk | 20 GB | 100+ GB (varies by artifact volume) |

### Network Requirements

**Development/Testing:**
- Default ports: 8080 (HTTP), 8443 (HTTPS)
- These ports must be available on your host

**Production:**
- Ports 80 (HTTP) and 443 (HTTPS) must be available
- For Let's Encrypt TLS: Port 443 must be publicly accessible
- DNS: Domain name must resolve to server's public IP

### Supported Platforms

Magpie is tested on:
- Linux (Ubuntu 22.04+, Debian 12+, RHEL 9+)
- macOS (ARM64 and x86_64)
- Windows (via WSL2)

Container images support:
- amd64 (x86_64)
- arm64 (aarch64)

## Installation Options

Choose the installation method that best fits your use case:

| Method | Best For | Setup Time |
|--------|----------|------------|
| [Docker Compose (Production)](#production-deployment-docker-compose) | Production deployments with TLS | 10 minutes |
| [Docker Compose (Development)](#development-deployment-docker-compose) | Local testing and development | 5 minutes |
| [Native Python](#native-python-installation) | CLI-only or custom setups | 2 minutes |
| [Development Setup](#development-setup) | Contributing to Magpie | 10 minutes |

## Quick Start (5 Minutes)

Get Magpie running quickly for testing:

```bash
# 1. Clone the repository
git clone https://github.com/SouthwestCCDC/magpie.git
cd magpie

# 2. Start services
docker compose up -d

# 3. Initialize storage and create admin token
docker compose exec magpie magpie-ctl init

# 4. Save the admin token displayed (you'll need it later)

# 5. Install CLI client
uv pip install git+https://github.com/SouthwestCCDC/magpie.git

# 6. Configure client
export MAGPIE_SERVER=http://localhost:8080
export MAGPIE_TOKEN=mgp_your_admin_token_here

# 7. Test: Upload your first artifact
echo "Hello Magpie" > test.txt
magpie push test.txt --to demo/hello

# 8. Verify: Download the artifact
magpie get demo/hello:latest
```

Success! You've uploaded and downloaded your first artifact.

## Production Deployment (Docker Compose)

For production deployments with automatic TLS via Let's Encrypt:

### Step 1: Clone Repository

```bash
git clone https://github.com/SouthwestCCDC/magpie.git
cd magpie
```

### Step 2: Configure Environment

Create a `.env` file in the repository root:

```bash
# Required: Your domain name for TLS
MAGPIE_DOMAIN=magpie.example.com

# Required: Persistent storage location
MAGPIE_DATA_DIR=/var/lib/magpie/artifacts

# Optional: Custom ports (defaults shown)
MAGPIE_HTTP_PORT=80
MAGPIE_HTTPS_PORT=443

# Optional: Authentik SSO (see docs/authentik-setup.md)
# AUTHENTIK_HOST=authentik.example.com
```

### Step 3: Create Storage Directory

```bash
# Create data directory with appropriate permissions
sudo mkdir -p /var/lib/magpie/artifacts
sudo chown -R $(id -u):$(id -g) /var/lib/magpie/artifacts
```

### Step 4: Start Services

```bash
# Start in production mode
docker compose -f docker-compose.prod.yml up -d

# Check logs
docker compose -f docker-compose.prod.yml logs -f
```

### Step 5: Initialize Storage

```bash
# Create storage structure and admin token
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl init

# IMPORTANT: Save the admin token displayed
# It looks like: mgp_ADMIN_abc123def456...
# This token is only shown once and cannot be recovered
```

### Step 6: Verify Installation

```bash
# Check service health
curl https://magpie.example.com/health

# Expected output: {"status": "healthy"}
```

### Step 7: Install CLI Client

On your workstation (not the server):

```bash
# Install CLI
uv pip install git+https://github.com/SouthwestCCDC/magpie.git

# Configure
magpie config --server https://magpie.example.com --token mgp_ADMIN_your_token

# Test
magpie push /path/to/file.tar.gz --to test/artifact
```

### Production Notes

**Port conflicts:** If ports 80 or 443 are in use by nginx, Apache, or another service:
1. Stop the conflicting service: `sudo systemctl stop nginx`
2. OR customize ports in `.env`: `MAGPIE_HTTP_PORT=8080 MAGPIE_HTTPS_PORT=8443`

**Let's Encrypt rate limits:** Let's Encrypt allows 50 certificates per domain per week.
For testing TLS setup, use staging environment first:

Edit `Caddyfile.prod` and add:
```caddyfile
{$MAGPIE_DOMAIN} {
    tls {
        ca https://acme-staging-v02.api.letsencrypt.org/directory
    }
    # ... rest of config
}
```

**Certificate persistence:** The `caddy_data` Docker volume stores TLS certificates.
Preserve this volume to avoid requesting duplicate certificates:

```bash
# Backup certificates
docker run --rm -v magpie_caddy_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/caddy-certs-backup.tar.gz /data

# Restore certificates
docker run --rm -v magpie_caddy_data:/data -v $(pwd):/backup alpine \
  tar xzf /backup/caddy-certs-backup.tar.gz -C /
```

## Development Deployment (Docker Compose)

For local testing without TLS:

```bash
# Clone repository
git clone https://github.com/SouthwestCCDC/magpie.git
cd magpie

# Start services (uses ports 8080/8443 by default)
docker compose up -d

# Initialize storage
docker compose exec magpie magpie-ctl init

# Services available at:
# - http://localhost:8080/health
# - https://localhost:8443/health (self-signed cert)
```

Custom ports:

```bash
# Use different ports
MAGPIE_HTTP_PORT=9000 MAGPIE_HTTPS_PORT=9443 docker compose up -d
```

## Native Python Installation

Install the CLI client without Docker:

### Using uv (Recommended)

```bash
# Install from latest release
uv pip install git+https://github.com/SouthwestCCDC/magpie.git

# Install specific version
uv pip install git+https://github.com/SouthwestCCDC/magpie.git@v1.0.0

# Verify installation
magpie --version
magpie-ctl --version
```

### Using pip

```bash
# Install from latest release
pip install git+https://github.com/SouthwestCCDC/magpie.git

# Install specific version
pip install git+https://github.com/SouthwestCCDC/magpie.git@v1.0.0
```

### Available Releases

Check [GitHub Releases](https://github.com/SouthwestCCDC/magpie/releases) for available versions.

### Post-Installation Configuration

```bash
# Configure client
magpie config --server https://magpie.example.com --token mgp_your_token

# Verify configuration
magpie config --show
```

Configuration is stored in `~/.magpie/config.toml`.

## Development Setup

For contributing to Magpie:

### Step 1: Clone and Install Dependencies

```bash
# Clone repository
git clone https://github.com/SouthwestCCDC/magpie.git
cd magpie

# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync
```

### Step 2: Run Server Locally

```bash
# Start FastAPI server with hot reload
uv run uvicorn magpie.server.app:app --reload

# Server available at http://localhost:8000
```

### Step 3: Run CLI from Source

```bash
# Client CLI
uv run magpie --help

# Server admin CLI
uv run magpie-ctl --help
```

### Step 4: Run Tests

```bash
# Unit tests
uv run pytest tests/unit/ -v

# Integration tests
uv run pytest tests/integration/ -v

# End-to-end tests (requires docker-compose)
uv run pytest tests/e2e/ -v

# All tests with coverage
uv run pytest --cov=magpie
```

### Step 5: Run Linting

```bash
# Check code style
uv run ruff check src/ tests/

# Auto-fix issues
uv run ruff check --fix src/ tests/

# Format code
uv run ruff format src/ tests/

# Security scan
uv run bandit -r src/
```

### Step 6: Run Full Stack with Docker

```bash
# Start Caddy + Magpie
docker compose up --build

# View logs
docker compose logs -f
```

## Client Configuration

The Magpie CLI can be configured via multiple methods:

### Configuration File

Create `~/.magpie/config.toml`:

```toml
[client]
server = "https://magpie.example.com"
token = "mgp_your_token_here"
```

### Environment Variables

Environment variables override config file settings:

```bash
export MAGPIE_SERVER=https://magpie.example.com
export MAGPIE_TOKEN=mgp_your_token_here
export MAGPIE_TIMEOUT=600  # seconds (or "5m", "1h30m")
```

### CLI Flags

CLI flags override all other configuration:

```bash
magpie --server https://magpie.example.com --token mgp_your_token push file.tar.gz --to path
```

### Configuration Precedence

1. CLI flags (highest priority)
2. Environment variables
3. Config file
4. Defaults (lowest priority)

**Note:** The `--timeout` setting can only be set via CLI flag or `MAGPIE_TIMEOUT` environment variable. It is intentionally not read from the config file.

### Managing Configuration

```bash
# Set server and token
magpie config --server https://magpie.example.com --token mgp_abc123

# Show current configuration
magpie config --show

# Clear all configuration
magpie config --clear
```

## Token Management

### Getting a Token

Contact your Magpie administrator to obtain an authentication token.

### Token Scopes

| Scope | Capabilities |
|-------|--------------|
| `read` | List artifacts, download files, view metadata |
| `write` | All read permissions + upload, tag, untag, amend |
| `admin` | All write permissions + manage tokens, run GC, flush tags |

### Creating Additional Tokens (Admin Only)

```bash
# Create read-only token
docker compose exec magpie magpie-ctl token create --name ci-reader --scope read

# Create write token
docker compose exec magpie magpie-ctl token create --name deployer --scope write

# Create admin token
docker compose exec magpie magpie-ctl token create --name ops-admin --scope admin

# List all tokens
docker compose exec magpie magpie-ctl token list

# Revoke a token
docker compose exec magpie magpie-ctl token revoke ci-reader
```

### Custom Admin Token

Specify a custom admin token during initialization:

```bash
# Initialize with specific admin token
docker compose exec magpie magpie-ctl init --admin-token mgp_ADMIN_your_custom_token_here
```

**Security requirements for custom tokens:**
- Must start with `mgp_ADMIN_`
- Should have at least 32 characters of entropy after prefix
- Use cryptographic random generation: `openssl rand -base64 32`
- Never use predictable patterns or dictionary words

### Regenerating Admin Token

```bash
# Revoke existing admin token and generate new one
docker compose exec magpie magpie-ctl init --reset-admin-token

# Or set a specific new token
docker compose exec magpie magpie-ctl init --reset-admin-token --admin-token mgp_ADMIN_new_token
```

## Verification and Testing

After installation, verify everything works:

### 1. Check Service Health

```bash
# HTTP health check
curl http://localhost:8080/health

# HTTPS health check (production)
curl https://magpie.example.com/health

# Expected output
{"status": "healthy"}
```

### 2. Upload Test Artifact

```bash
# Create test file
echo "Test artifact" > test.txt

# Upload
magpie push test.txt --to testing/verify

# Expected output
Uploaded: abc12345...
Hash ref: @abc12345
Download: https://magpie.example.com/artifacts/testing/verify/latest
```

### 3. List Artifacts

```bash
# List versions
magpie ls testing/verify

# Expected output (table format)
HASH         TAGS      UPLOADED_BY    UPLOADED_AT
@abc12345    latest    admin          2024-01-21 10:30:00
```

### 4. Download Artifact

```bash
# Download by tag
magpie get testing/verify:latest

# Verify content
cat verify
# Expected: Test artifact
```

### 5. Test Tagging

```bash
# Create tag
magpie tag testing/verify:latest --as stable

# List again (should show both tags)
magpie ls testing/verify
```

### 6. View Metadata

```bash
# Show artifact info
magpie info testing/verify:stable

# Expected output
Hash:        abc12345...
Hash Ref:    @abc12345
Uploaded By: admin
Uploaded At: 2024-01-21 10:30:00
Tags:        latest, stable
```

## Common Installation Issues

### Port Already in Use

**Symptom:** `Error starting userland proxy: listen tcp4 0.0.0.0:80: bind: address already in use`

**Solutions:**

1. **Find conflicting service:**
   ```bash
   sudo lsof -i :80
   sudo lsof -i :443
   ```

2. **Stop conflicting service:**
   ```bash
   sudo systemctl stop nginx
   sudo systemctl stop apache2
   ```

3. **OR use custom ports:**
   ```bash
   # In .env file or environment
   MAGPIE_HTTP_PORT=8080
   MAGPIE_HTTPS_PORT=8443
   docker compose -f docker-compose.prod.yml up -d
   ```

### Permission Denied on Storage Directory

**Symptom:** `PermissionError: [Errno 13] Permission denied: '/data/artifacts'`

**Solutions:**

1. **Check directory ownership:**
   ```bash
   ls -ld /var/lib/magpie/artifacts
   ```

2. **Fix ownership:**
   ```bash
   sudo chown -R $(id -u):$(id -g) /var/lib/magpie/artifacts
   ```

3. **OR run with correct user (in docker-compose):**
   The Magpie container automatically handles user mapping, but ensure the host directory is writable.

### TLS Certificate Issues

**Symptom:** `acme: error: 400 ... CAA record for domain.com prevents issuance`

**Solutions:**

1. **Check DNS CAA records:**
   ```bash
   dig CAA magpie.example.com
   ```

2. **Verify domain resolves:**
   ```bash
   dig A magpie.example.com
   ```

3. **Use staging for testing:**
   Edit `Caddyfile.prod`:
   ```caddyfile
   tls {
       ca https://acme-staging-v02.api.letsencrypt.org/directory
   }
   ```

4. **OR use self-signed certificates:**
   ```caddyfile
   tls internal
   ```

### Client Connection Refused

**Symptom:** `Error: Connection refused`

**Solutions:**

1. **Verify server is running:**
   ```bash
   docker compose ps
   ```

2. **Check server URL configuration:**
   ```bash
   magpie config --show
   # Ensure server URL matches actual deployment
   ```

3. **Test connectivity:**
   ```bash
   curl http://localhost:8080/health
   ```

4. **Check firewall:**
   ```bash
   sudo ufw status
   # Ensure ports 80/443 or custom ports are allowed
   ```

### Token Authentication Fails

**Symptom:** `401 Unauthorized`

**Solutions:**

1. **Verify token is set:**
   ```bash
   echo $MAGPIE_TOKEN
   magpie config --show
   ```

2. **Check token format:**
   - Admin tokens: `mgp_ADMIN_...`
   - Regular tokens: `mgp_...`
   - Must be at least 20 characters total

3. **Verify token exists:**
   ```bash
   docker compose exec magpie magpie-ctl token list
   ```

4. **Create new token if needed:**
   ```bash
   docker compose exec magpie magpie-ctl token create --name test --scope write
   ```

### Database Locked

**Symptom:** `sqlite3.OperationalError: database is locked`

**Solutions:**

1. **Check for multiple Magpie instances:**
   ```bash
   docker compose ps
   # Should only show one magpie container
   ```

2. **Restart service:**
   ```bash
   docker compose restart magpie
   ```

3. **Check for stale lock file:**
   ```bash
   # Inside container
   docker compose exec magpie ls -l /data/artifacts/.magpie.db-journal
   # If journal exists but no writes happening, remove it
   ```

## Next Steps

After successful installation:

1. **Read the User Guide:** See [docs/user-guide.md](user-guide.md) for complete CLI reference
2. **Set up backups:** See [docs/backup-restore.md](backup-restore.md) for backup procedures
3. **Configure SSO (optional):** See [docs/authentik-setup.md](authentik-setup.md) for Authentik integration
4. **Integrate with Ansible (optional):** See [docs/ansible-integration.md](ansible-integration.md) for playbook examples
5. **Production hardening:** Review security settings in `docker-compose.prod.yml`

## Getting Help

- **Documentation:** Check `docs/` directory for detailed guides
- **GitHub Issues:** Report bugs or request features at [SouthwestCCDC/magpie](https://github.com/SouthwestCCDC/magpie/issues)
- **Health check:** Run `curl http://localhost:8080/health` to verify service status

---
(AI-generated via Claude Code w/ Opus 4.5)
