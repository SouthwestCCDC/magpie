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
docker compose logs magpie | grep "ADMIN TOKEN"
```

2. Create CI tokens:
```bash
docker compose exec magpie magpie-ctl token create --name ci-deployer --scope write
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
