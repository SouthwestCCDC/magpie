# Production Readiness Checklist

Quick reference for deploying Magpie to production. See also: [backup-restore.md](backup-restore.md), [user-guide.md](user-guide.md).

## Pre-Deployment

- [ ] DNS configured for `MAGPIE_DOMAIN` (resolves to server IP)
- [ ] Firewall allows port 443 (HTTPS) and 80 (HTTP redirect)
- [ ] Backup storage provisioned (see [backup-restore.md](backup-restore.md))
- [ ] Hardware meets minimum requirements (Docker host with persistent storage)

## Required Configuration

Set in `.env` or environment:

- [ ] `MAGPIE_DOMAIN` - Domain name for TLS (required by `docker-compose.prod.yml` line 44)
- [ ] `MAGPIE_DATA_DIR` - Host path for artifact storage (default: `./data/artifacts`)

## Security Hardening

Verify in `docker-compose.prod.yml` and `Caddyfile.prod`:

- [ ] HTTPS enforced via Let's Encrypt (automatic in `Caddyfile.prod`)
- [ ] `MAGPIE_DEBUG=false` (line 74 of `docker-compose.prod.yml`)
- [ ] Security headers enabled (HSTS, X-Frame-Options - `Caddyfile.prod` lines 172-198)
- [ ] Admin token created and stored securely

Create initial admin token:
```bash
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl init --reset-admin-token
```

## Optional Configuration

If needed, configure in `.env`:

- [ ] `MAGPIE_ALLOWED_CIDRS` - IP allowlist for read-only access (`.env.example` lines 110-129)
- [ ] `AUTHENTIK_HOST` - SSO integration (see [authentik-setup.md](authentik-setup.md))
- [ ] `MAGPIE_RETENTION_DAYS` - GC retention (default: 90 days, `.env.example` line 55)
- [ ] `MAGPIE_SENTRY_DSN` - Error tracking (`.env.example` line 93)
- [ ] `MAGPIE_OTEL_*` - Distributed tracing (`.env.example` lines 95-104)

## Deployment

```bash
# Start services
docker compose -f docker-compose.prod.yml up -d

# Wait for health check
docker compose -f docker-compose.prod.yml ps

# Create initial admin token (store output securely)
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl init --reset-admin-token
```

## Post-Deployment Validation

- [ ] Health endpoint responding: `curl https://$MAGPIE_DOMAIN/health`
- [ ] Status endpoint shows storage stats: `curl https://$MAGPIE_DOMAIN/api/v1/status`
- [ ] HTTPS enforced (HTTP redirects to HTTPS)
- [ ] Upload works: `magpie push test.txt --to test/artifact`
- [ ] Download works: `magpie get test/artifact:latest`
- [ ] Authentication working (401 without token, 200 with valid token)

## Backup Configuration

See [backup-restore.md](backup-restore.md) for detailed procedures.

- [ ] Automated backup configured (systemd timer or cron)
- [ ] Backup script tested (verify rsync excludes `.tmp/`)
- [ ] Restore procedure tested (non-destructive dry run)
- [ ] Database backup verified (SQLite `.backup` command)

## Operational Readiness

- [ ] Monitoring configured (health endpoint checks)
- [ ] Log aggregation configured (if using `MAGPIE_LOG_FORMAT=json`)
- [ ] Token rotation procedure documented
- [ ] GC schedule determined (manual via `magpie-ctl gc` or external cron)
