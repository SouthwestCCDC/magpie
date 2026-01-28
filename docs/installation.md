# Installation Guide

## Quick Start (Development)

```bash
git clone https://github.com/SouthwestCCDC/magpie
cd magpie
docker compose up --build
```

Server runs at `http://localhost:8080` (change via `MAGPIE_HTTP_PORT`).

## Production Deployment

```bash
export MAGPIE_DOMAIN=magpie.example.com
export MAGPIE_DATA_DIR=./data
docker compose -f docker-compose.prod.yml up -d
```

Requires: domain name for TLS (auto-provisioned via Let's Encrypt).

## First Steps

1. Get admin token:
```bash
docker compose -f docker-compose.prod.yml logs magpie | grep "ADMIN TOKEN"
```

The admin token (format: `mgp_ADMIN_...`) is generated once on first initialization and written to the container logs (you can re-run the `logs` command to see it until logs are rotated). Save it securely - it's primarily used to bootstrap and perform admin actions (like creating additional tokens), not for everyday CLI usage.

**Security Note:** The initial admin token appears in container logs. As a best practice, create a new admin token and revoke the initial one after deployment:

```bash
# Create new admin token
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl token create --name ops-admin --scope admin

# Revoke the initial break-glass token (default name: "admin")
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl token revoke admin
```

See [Issue #387](https://github.com/SouthwestCCDC/magpie/issues/387) for tracking improvements to token initialization.

2. Create CI tokens:
```bash
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl token create --name ci-deployer --scope write
```

3. Install client:
```bash
uv pip install git+https://github.com/SouthwestCCDC/magpie
```

4. Configure client:
```bash
export MAGPIE_SERVER=https://magpie.example.com
export MAGPIE_TOKEN=mgp_your_token_here
```

5. Test:
```bash
curl https://magpie.example.com/health
echo "test" > test.txt
magpie push test.txt --to test/hello
magpie get test/hello:latest
```

## Configuration

Key environment variables (see [.env.example](../.env.example) for all):
- `MAGPIE_DOMAIN` - Domain for TLS
- `MAGPIE_DATA_DIR` - Storage path (default: `./data`)
- `MAGPIE_STORAGE_PATH` - Artifact storage (default: `/data/artifacts`)
- `MAGPIE_RETENTION_DAYS` - GC retention period (default: 90)
- `MAGPIE_DEBUG` - Verbose logging (default: false; never in production)

Next: [Production Checklist](production-checklist.md) → [User Guide](user-guide.md) → [Backup & Restore](backup-restore.md)

---

*(AI-generated via Claude Code w/ Sonnet 4.5)*
