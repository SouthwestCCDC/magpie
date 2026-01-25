# Production Readiness Checklist

Quick reference for deploying Magpie to production. See also: [backup-restore.md](backup-restore.md), [user-guide.md](user-guide.md).

## Pre-Deployment

- [ ] DNS configured for `MAGPIE_DOMAIN` (resolves to server IP)
- [ ] Firewall allows `MAGPIE_HTTPS_PORT` (default: 443) and `MAGPIE_HTTP_PORT` (default: 80)
- [ ] Backup storage provisioned (see [backup-restore.md](backup-restore.md))
- [ ] Hardware meets minimum requirements (Docker host with persistent storage)

## Required / Recommended Configuration

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

- [ ] `AUTHENTIK_HOST` - SSO integration (see [authentik-setup.md](authentik-setup.md))

**Note:** Additional environment variables (e.g., `MAGPIE_ALLOWED_CIDRS`, `MAGPIE_RETENTION_DAYS`, `MAGPIE_LOG_FORMAT`, `MAGPIE_SENTRY_DSN`, `MAGPIE_OTEL_*`) are supported by Magpie but require manual modification of `docker-compose.prod.yml` to pass them to the `magpie` service. By default, only `MAGPIE_STORAGE_PATH` and `MAGPIE_DEBUG` are configured in the production compose file. See `.env.example` and `config.py` for the full list of available settings.

## Deployment

```bash
# Start services (creates admin token on first start)
docker compose -f docker-compose.prod.yml up -d

# Capture the admin token from first-start logs (store securely)
docker compose -f docker-compose.prod.yml logs magpie | grep -A1 "ADMIN TOKEN" | tail -n1

# Wait for health check
docker compose -f docker-compose.prod.yml ps
```

**Note:** The `entrypoint.sh` script automatically runs `magpie-ctl init` on first start when the database doesn't exist. The admin token is printed in the container logs and is only visible once. If you miss capturing it, use the token rotation procedure below to generate a new one.

### Token Rotation / Recovery

To rotate or recover the break-glass admin token:

```bash
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl init --reset-admin-token
```

**Warning:** Using `--reset-admin-token` will revoke the existing break-glass admin token (named "admin"). Other admin-scoped tokens with different names will remain valid. Only use this during initial setup mistakes, security incidents, or planned token rotation.

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
