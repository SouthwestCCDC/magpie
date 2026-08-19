# Quick Start Guide

Get a **real** Magpie server running in 5 minutes using the installer script. If you just want
to try Magpie on a throwaway local stack first, use the
[5-Minute Quick Start](../README.md#5-minute-quick-start) in the README instead.

**Prerequisites:** Debian 12/13, git, Docker with Compose v2, curl, `uv`
([install](https://docs.astral.sh/uv/)). You do not need a system Python 3.13 -- `uv` fetches
its own for the CLI.

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
# Installs `magpie` and `magpie-ctl` into ~/.local/bin, isolated, with their own Python
uv tool install git+https://github.com/SouthwestCCDC/magpie
export MAGPIE_SERVER=http://localhost:8080
export MAGPIE_TOKEN=mgp_ADMIN_...  # Token from step 1
magpie status  # Verify connection
```

Keep the client's minor version matched to the server's -- an older CLI is rejected with
`426 Upgrade Required` ([details](api-compatibility.md#version-coupling)). Working from a repo
checkout, `uv sync` plus `uv run magpie ...` does the same job for development.

## 3. Upload and Download

```bash
echo "Hello Magpie" > hello.txt
magpie push hello.txt --to demo/greeting
magpie ls demo/greeting
magpie get demo/greeting:latest -o roundtrip.txt
cat roundtrip.txt
```

Without `-o`, the download is named after the artifact (`greeting`), not the file you uploaded:
content is addressed by hash and the original filename is not stored.

## Common Patterns

```bash
# Tag management
magpie push app.tar.gz --to apps/myapp
magpie tag apps/myapp:latest --as stable
magpie tag apps/myapp:@abcdef12 --as v1.0.0  # Pin specific version (note the `:` before `@`)

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

- [Architecture](architecture.md) - what's inside the container, and dev vs production
- [Installation Guide](installation.md) - Advanced deployment options and reverse-proxy setup
- [Configuration Reference](configuration.md) - every environment variable
- [User Guide](user-guide.md) - Complete CLI reference
- [Production Checklist](production-checklist.md) - Pre-deployment verification

---

*(AI-generated via Claude Code w/ Sonnet 4.5)*
