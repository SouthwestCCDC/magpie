# Installation Guide

This guide covers deploying Magpie artifact storage using Docker Compose.

## Prerequisites

- **Docker with Compose v2.1+** (required for healthcheck conditions in `docker-compose.prod.yml`)
- **Python 3.13+** (only if installing client CLI locally, see [pyproject.toml](../pyproject.toml))

For production deployments with Let's Encrypt auto-TLS:
- Public DNS record pointing to your server
- Ports 80 and 443 accessible from the internet

## Quick Start (Development)

Development setup runs on localhost with HTTP only.

```bash
# Clone the repository
git clone https://github.com/SouthwestCCDC/magpie
cd magpie

# Start services (Caddy + FastAPI)
docker compose up --build
```

The server will be available at `http://localhost:8080` (configurable via `MAGPIE_HTTP_PORT`).

Default ports from [docker-compose.yml](../docker-compose.yml):
- HTTP: `8080` (configured via `MAGPIE_HTTP_PORT`, default `8080`)
- HTTPS: `8443` (configured via `MAGPIE_HTTPS_PORT`, default `8443`)

## Production Deployment

Production deployment requires a domain name for TLS.

```bash
# Set required environment variables
export MAGPIE_DOMAIN=magpie.example.com
export MAGPIE_DATA_DIR=./data  # Host path for persistent storage (contains artifacts/ and magpie.db)

# Start with production configuration
docker compose -f docker-compose.prod.yml up -d
```

**Required environment variables** (see [docker-compose.prod.yml](../docker-compose.prod.yml)):
- `MAGPIE_DOMAIN`: Domain name for TLS (e.g., `magpie.swccdc.com`)
- `MAGPIE_DATA_DIR`: Host directory for data storage (default: `./data` in docker-compose.yml). This is the HOST path - can be relative (e.g., `./data`) or absolute (e.g., `/opt/magpie/data`). Inside the container, it's always mounted at `/data`. The directory will contain the `artifacts/` subdirectory and `magpie.db` database file

Production setup automatically provisions TLS certificates via Let's Encrypt. The `caddy_data` volume persists certificates across container restarts.

**Port conflicts**: Production binds to ports 80 and 443 by default. Stop any conflicting services (nginx, Apache, other Caddy instances) or customize ports via `MAGPIE_HTTP_PORT` and `MAGPIE_HTTPS_PORT`.

## Server Initialization

The container automatically runs `magpie-ctl init` on first startup (see [entrypoint.sh](../entrypoint.sh)). This creates storage directories, initializes the database, and generates a break-glass admin token.

**For Docker Compose deployments**, the admin token is printed to container logs on first startup. Retrieve it with:

```bash
# View container logs to find the admin token (only shown once)
docker compose logs magpie | grep "ADMIN TOKEN"
```

**For manual initialization** (if needed outside Docker):

```bash
# Initialize storage and generate admin token
magpie-ctl init
```

The admin token (format: `mgp_ADMIN_...`) is displayed only once. Save it securely - it's required for CLI authentication and creating additional tokens.

**Creating additional tokens:**

```bash
# Create a write-scoped token for CI/CD
docker compose exec magpie magpie-ctl token create --name ci-deployer --scope write

# Create another admin token
docker compose exec magpie magpie-ctl token create --name ops-admin --scope admin
```

## Client Configuration

The `magpie` CLI can be installed via pip or run directly from the repository.

**Install from git:**
```bash
uv pip install git+https://github.com/SouthwestCCDC/magpie
```

**Configure authentication:**
```bash
export MAGPIE_SERVER=https://magpie.example.com
export MAGPIE_TOKEN=mgp_your_token_here
```

**Verify connectivity:**
```bash
magpie config  # Shows resolved configuration
```

## Environment Variables

All server configuration is optional except `MAGPIE_DOMAIN` in production. See [.env.example](../.env.example) for the complete list.

Key variables from [.env.example](../.env.example):
- `MAGPIE_STORAGE_PATH`: Root directory for artifacts (default: `/data/artifacts`)
- `MAGPIE_DATABASE_PATH`: Path to SQLite database file (default: `/data/magpie.db`)
- `MAGPIE_RETENTION_DAYS`: Days before untagged blobs are eligible for GC (default: `90`)
- `MAGPIE_DEBUG`: Enable verbose logging (default: `false`, **never enable in production**)
- `MAGPIE_MAX_UPLOAD_SIZE`: Maximum upload size in bytes (default: no limit)

## Verification

After deployment, verify the installation:

```bash
# Check server health
curl https://magpie.example.com/health

# Push a test artifact
echo "test" > test.txt
magpie push test.txt --to test/hello

# Retrieve the artifact
magpie get test/hello:latest

# List versions
magpie ls test/hello
```

## Next Steps

- **API access**: See [user-guide.md](user-guide.md) for complete CLI and API documentation
- **SSO setup**: See [authentik-setup.md](authentik-setup.md) for browser-based authentication
- **Backup**: See [backup-restore.md](backup-restore.md) for backup procedures

---
*Generated with AI assistance (Claude Code w/ Sonnet 4.5).*
