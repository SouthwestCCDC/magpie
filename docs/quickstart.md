# Quick Start Guide

Get Magpie running in 5 minutes using the installer script.

**Prerequisites:** Debian 12/13, Python 3.13+, git, Docker with Compose v2, curl, `uv` ([install](https://docs.astral.sh/uv/))

## 1. Deploy Server

```bash
git clone https://github.com/SouthwestCCDC/magpie
cd magpie
sudo ./scripts/magpie-deploy.sh install --tls-mode off --noninteractive
# Copy the admin token from install output. If lost, reset with:
# docker exec -it magpie-magpie-1 magpie-ctl init --reset-admin-token
```

Use `--tls-mode off` for local testing or behind a reverse proxy. For Let's Encrypt: `--tls-mode auto --domain magpie.example.com`.

See [Installation Guide](installation.md) for TLS options and advanced configuration.

## 2. Install Client

```bash
uv pip install -e .
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
URL=$(magpie url apps/myapp:stable)
curl -H "Authorization: Bearer $MAGPIE_TOKEN" -o myapp.tar.gz "$URL"

# Provenance
magpie push build.tar.gz --to builds/app --source-uri "https://github.com/org/repo/commit/abc123"
magpie info builds/app:latest
```

## Troubleshooting

- **Connection refused:** `sudo ./scripts/magpie-deploy.sh status`
- **Auth errors:** `magpie status`
- **Port conflict:** Reinstall with `--http-port 8888`

## Next Steps

- [Installation Guide](installation.md) - Advanced deployment options and TLS configuration
- [User Guide](user-guide.md) - Complete CLI reference
- [Production Checklist](production-checklist.md) - Pre-deployment verification

---

*(AI-generated via Claude Code w/ Sonnet 4.5)*
