# Production Readiness Checklist

Quick reference for deploying Magpie to production. See also: [backup-restore.md](backup-restore.md), [user-guide.md](user-guide.md).

## Pre-Deployment

- [ ] DNS configured for `MAGPIE_DOMAIN` (resolves to server IP)
- [ ] Firewall allows `MAGPIE_HTTPS_PORT` (default: 443) and `MAGPIE_HTTP_PORT` (default: 80)
- [ ] Backup storage provisioned (see [backup-restore.md](backup-restore.md))
- [ ] Hardware meets minimum requirements (Docker host with persistent storage)

## Required Configuration

Set in `.env` or environment:

- [ ] `MAGPIE_DOMAIN` - Domain name for TLS (required by `docker-compose.prod.yml`)
- [ ] `MAGPIE_DATA_DIR` - Host path for artifact storage (recommended; defaults to `./data/artifacts` if not set)

## Security Hardening

Verify in `docker-compose.prod.yml` and `Caddyfile.prod`:

- [ ] HTTPS enforced via Let's Encrypt (automatic in `Caddyfile.prod`)
- [ ] `MAGPIE_DEBUG=false` (set in environment for magpie service)
- [ ] Security headers enabled (HSTS, X-Frame-Options configured in `Caddyfile.prod`)
- [ ] Admin token created and stored securely (see Deployment section below)

## Optional Configuration

If needed, configure in `.env`:

- [ ] `MAGPIE_ALLOWED_CIDRS` - IP allowlist for read-only access (see `.env.example`)
- [ ] `AUTHENTIK_HOST` - SSO integration (see [authentik-setup.md](authentik-setup.md))
- [ ] `MAGPIE_RETENTION_DAYS` - GC retention (default: 90 days)
- [ ] `MAGPIE_SENTRY_DSN` - Error tracking
- [ ] `MAGPIE_OTEL_*` - Distributed tracing configuration

## Deployment

```bash
# Start services
docker compose -f docker-compose.prod.yml up -d

# Wait for health check
docker compose -f docker-compose.prod.yml ps

# Create initial admin token (store output securely)
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl init
```

### Token Rotation / Recovery

To rotate or recover admin tokens (this will revoke existing admin tokens):

```bash
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl init --reset-admin-token
```

**Warning:** Using `--reset-admin-token` will invalidate all existing admin tokens. Only use during initial setup mistakes, security incidents, or planned rotation.

## Post-Deployment Validation

- [ ] Health endpoint responding: `curl https://$MAGPIE_DOMAIN/health`
- [ ] Status endpoint shows storage stats: `curl -H "Authorization: Bearer $MAGPIE_ADMIN_TOKEN" https://$MAGPIE_DOMAIN/api/v1/status`
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

---

*This documentation was generated with AI assistance (Claude Code w/ Sonnet 4.5)*
