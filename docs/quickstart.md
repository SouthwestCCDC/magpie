# Quick Start Guide

Get a **real** Magpie server running in 5 minutes from the release's digest-pinned Docker
Compose assets. The installer is optional for hosts where you want systemd and account
management. For a throwaway local stack, use the
[5-Minute Quick Start](../README.md#5-minute-quick-start) in the README instead.

**Prerequisites:** Debian 12/13, git, Docker with Compose v2, curl, `uv`
([install](https://docs.astral.sh/uv/)). You do not need a system Python 3.13 -- `uv` fetches
its own for the CLI.

## 1. Deploy Server

```bash
base=https://github.com/SouthwestCCDC/magpie/releases/latest/download
curl -fsSLO "$base/docker-compose.yml" -fsSLO "$base/env.example" -fsSLO "$base/SHA256SUMS"
grep -E ' (docker-compose\.yml|env\.example)$' SHA256SUMS | sha256sum -c
cp env.example .env
echo 'MAGPIE_ADMIN_TOKEN_SINK=file' >> .env
# Edit .env for your data directory, runtime uid/gid, and trusted proxy.
docker compose up -d
```

The release's `docker-compose.yml` pins its bundled image by digest. Keep it with the matching
`env.example` and `SHA256SUMS`; see [Release Assets](releases.md#release-assets). Magpie serves
plain HTTP on `:8080` only. For a public deployment, put an external reverse proxy in front of
it for TLS and forward to `http://127.0.0.1:8080` (or the address configured by
`MAGPIE_BIND_IP`).

With the default `MAGPIE_DATA_DIR=./data` and file sink, retrieve the first-boot admin token
with:

```bash
sudo cat data/admin-token
```

See [Installation Guide](installation.md) for advanced configuration.

The installer is an optional alternative when you want systemd units and account management:
clone the repository and run `sudo ./scripts/magpie-deploy.sh install --noninteractive`.

## 2. Install Client

```bash
MAGPIE_SERVER=${MAGPIE_SERVER:-http://localhost:8080}
# Client and server minor versions must match.
V="$(curl -fsS "${MAGPIE_SERVER}/health" | sed -E 's/.*"version" *: *"([^"]+)".*/\1/; s/^([0-9]+\.[0-9]+\.[0-9]+)(a|b|rc)([0-9]+)$/\1-\2\3/')"
uv tool install "git+https://github.com/SouthwestCCDC/magpie@v${V}"
export MAGPIE_SERVER
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
magpie tag apps/myapp:@abcdef1234567890 --as v1.0.0  # Pin specific version (note the `:` before `@`)

# Scripting
magpie get apps/myapp:stable -o myapp.tar.gz
URL=$(magpie url apps/myapp:stable)
curl -H "Authorization: Bearer $MAGPIE_TOKEN" -o myapp.tar.gz "$URL"

# Provenance
magpie push build.tar.gz --to builds/app --source-uri "https://github.com/org/repo/commit/abc123"
magpie info builds/app:latest
```

## Troubleshooting

- **Connection refused:** run `docker compose ps`, then inspect `docker compose logs magpie`.
  If the service is stopped, run `docker compose up -d`.
- **Auth errors:** `magpie status`
- **Port conflict:** set `MAGPIE_HTTP_PORT=8888` in `.env` (and adjust `MAGPIE_BIND_IP` if
  needed), then run `docker compose up -d` and export
  `MAGPIE_SERVER=http://localhost:8888` for the client. Point any external reverse
  proxy upstream at the new port.

For installer-managed hosts, `sudo ./scripts/magpie-deploy.sh status` provides the equivalent
status check; configure `--http-port`/`--bind-ip` through the installer.

## Next Steps

- [Architecture](architecture.md) - what's inside the container, and dev vs production
- [Installation Guide](installation.md) - Advanced deployment options and reverse-proxy setup
- [Configuration Reference](configuration.md) - every environment variable
- [User Guide](user-guide.md) - Complete CLI reference
- [Production Checklist](production-checklist.md) - Pre-deployment verification

---

*(AI-generated via Claude Code w/ Sonnet 4.5; refreshed for v0.2.0 final via Devin; latest-release URLs, Compose troubleshooting, and health-derived client install updated via Devin; prerelease tag mapping updated via Devin; hash-ref width via Devin)*
