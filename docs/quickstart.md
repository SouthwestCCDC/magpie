# Quick Start Guide

Get Magpie running locally in 5 minutes.

**Prerequisites:** Docker and `uv` ([install guide](https://docs.astral.sh/uv/))

## 1. Start Server

```bash
git clone https://github.com/SouthwestCCDC/magpie
cd magpie
docker compose up -d
docker compose logs magpie | grep "ADMIN TOKEN"  # Save this token
```

## 2. Install Client

```bash
uv pip install git+https://github.com/SouthwestCCDC/magpie
export MAGPIE_SERVER=http://localhost:8080
export MAGPIE_TOKEN=mgp_ADMIN_...  # Token from step 1
magpie status  # Verify connection
```

## 3. Upload and Download

```bash
echo "Hello Magpie" > hello.txt
magpie push hello.txt --to demo/greeting
magpie ls demo/greeting
magpie get demo/greeting:latest
```

## Common Patterns

```bash
# Tag management
magpie push app.tar.gz --to apps/myapp
magpie tag apps/myapp:latest --as stable
magpie tag apps/myapp@abcdef12 --as v1.0.0  # Pin specific version

# Scripting
magpie get apps/myapp:stable -o myapp.tar.gz
URL=$(magpie url apps/myapp:stable)  # For curl/wget

# Provenance
magpie push build.tar.gz --to builds/app --source-uri "https://github.com/org/repo/commit/abc123"
magpie info builds/app:latest

# Token management
docker compose exec magpie magpie-ctl token create --name readonly --scope read
docker compose exec magpie magpie-ctl token create --name ci --scope write
```

## Troubleshooting

**Connection refused:** Check `docker compose ps` and `docker compose logs magpie`

**Auth errors:** Verify token with `magpie config`

**Port conflict:** Set `MAGPIE_HTTP_PORT=8888` and update `MAGPIE_SERVER` URL

## Next Steps

- [Installation Guide](installation.md) - Production deployment
- [User Guide](user-guide.md) - Complete CLI reference
- [Production Checklist](production-checklist.md) - Pre-deployment verification

---

*(AI-generated via Claude Code w/ Sonnet 4.5)*
