# Quick Start Guide

Get Magpie running in 5 minutes using the installer script.

**Prerequisites:** Debian 12/13, Python 3.13+, git, Docker with Compose v2, curl, `uv` ([install](https://docs.astral.sh/uv/))

## 1. Deploy Server

```bash
git clone https://github.com/SouthwestCCDC/magpie
cd magpie
sudo ./scripts/magpie-deploy.sh install --noninteractive
# Copy the admin token from install output. With the default
# MAGPIE_ADMIN_TOKEN_SINK=file, if it wasn't shown (or you lost it), read
# it instead:
# sudo cat /opt/magpie/data/admin-token
# To reset it: cd /opt/magpie && docker compose exec magpie magpie-ctl init --reset-admin-token
# (the new token is delivered through the same configured sink, i.e. also
# written to that file, not printed to the terminal)
```

Magpie serves plain HTTP only -- it does not terminate TLS itself. For a
public deployment, put a reverse proxy (nginx, Caddy, a cloud load
balancer, etc.) in front of it and forward to `http://127.0.0.1:8080`
(or whatever port `MAGPIE_HTTP_PORT`/`--http-port` was set to). If
`MAGPIE_BIND_IP`/`--bind-ip` was set to bind a specific host address
instead of the default (all interfaces), forward to that address instead
-- 127.0.0.1 won't be listening.

See [Installation Guide](installation.md) for advanced configuration.

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

- [Installation Guide](installation.md) - Advanced deployment options and reverse-proxy setup
- [User Guide](user-guide.md) - Complete CLI reference
- [Production Checklist](production-checklist.md) - Pre-deployment verification

---

*(AI-generated via Claude Code w/ Sonnet 4.5)*
