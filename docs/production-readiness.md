# Production Readiness Checklist

This checklist covers required configuration and setup steps before deploying Magpie to production. All items trace to actual code requirements.

## Container Runtime

- [ ] Mount storage directory at `/data/artifacts` (docker-compose.yml:8, 25, 39)
- [ ] Container has `gosu` installed (Dockerfile:23, entrypoint.sh:73)
- [ ] User/group ownership configured on `/data/artifacts` (entrypoint.sh:12, 18) or set via `MAGPIE_UID`/`MAGPIE_GID` (entrypoint.sh:9, 15)
- [ ] Directory exists for `MAGPIE_GC_LOCK_PATH` (config.py:35, default: `/var/run`)
- [ ] Database lock directory writable (entrypoint.sh:48-57)

## Storage Initialization

- [ ] Run `magpie-ctl init` to create directories (ctl/commands/init.py:66, 70)
- [ ] Save admin token output (ctl/commands/init.py:38, shown once only)
- [ ] Verify `storage_path/.tmp` created (config.py:94)
- [ ] Verify `storage_path/.magpie.db` created (config.py:96, ctl/commands/init.py:78)

## Network Configuration

- [ ] Set `MAGPIE_HTTP_PORT` (docker-compose.yml:21, default: 8080)
- [ ] Set `MAGPIE_HTTPS_PORT` (docker-compose.yml:22, default: 8443)
- [ ] Port 8000 exposed only internally between Caddy and magpie service (docker-compose.yml:44)
- [ ] Health check endpoint responding at `/health` (server/app.py:57)

## Authentication

- [ ] `MAGPIE_TOKEN` set in client environments (.env.example:151)
- [ ] IP allow-list configured if needed via `MAGPIE_ALLOWED_CIDRS` (config.py:60, Caddyfile:76)
- [ ] CIDR validation passes if `MAGPIE_ALLOWED_CIDRS` set (config.py:84)
- [ ] Caddy forward_auth validates bearer tokens at `/api/v1/auth/validate` (Caddyfile:88, 115)

## Python Environment

- [ ] Python 3.13+ available (pyproject.toml:7)
- [ ] Dependencies installed via `uv sync` (Dockerfile:15, 32)
- [ ] Application installed at `/app/.venv/bin/python` (entrypoint.sh:97, 99)

## Service Startup

- [ ] Uvicorn starts on port 8000 (Dockerfile:45)
- [ ] Logging configured via `configure_logging()` (server/app.py:25)
- [ ] Storage directories validated by entrypoint (entrypoint.sh:48-57)
- [ ] Database auto-initialized on first run with flock (entrypoint.sh:78-111)

## Optional Configuration

- [ ] `MAGPIE_MAX_UPLOAD_SIZE` set if size limits needed (config.py:41)
- [ ] `MAGPIE_RETENTION_DAYS` configured (config.py:31, default: 90)
- [ ] `MAGPIE_LOG_FORMAT` set to `json` for production (config.py:48)
- [ ] `MAGPIE_SENTRY_DSN` configured for error tracking (config.py:51)
- [ ] `MAGPIE_OTEL_ENABLED` and `MAGPIE_OTEL_ENDPOINT` for tracing (config.py:52, 53)
- [ ] `MAGPIE_S3_BUCKET` configured for backup (config.py:44)

## Verification

- [ ] Health check returns {"status": "ok"} (server/app.py:60)
- [ ] Token validation endpoint returns 200/401 (Caddyfile:12)
- [ ] Upload endpoint accessible with auth (Caddyfile:23)
- [ ] Static file serving works at `/artifacts/*` (Caddyfile:25)

---

All file references verified against commit 76d07f2.

(AI-generated via Claude Code w/ Opus 4.5)
