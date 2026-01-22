# Production Readiness Checklist

Use this checklist to validate that your Magpie deployment is ready for production.

## Pre-Deployment

### Infrastructure

- [ ] Server has persistent storage mounted at `MAGPIE_DATA_DIR` (recommended: `/opt/magpie/data` or `/var/lib/magpie`)
- [ ] Storage has sufficient capacity for your artifact volume (include buffer for retention period)
- [ ] Network connectivity verified: server can reach public internet for Let's Encrypt TLS
- [ ] Firewall allows inbound traffic on ports 80 and 443 (or custom `MAGPIE_HTTP_PORT` / `MAGPIE_HTTPS_PORT`)
- [ ] SSL/TLS certificate ready (automatic Let's Encrypt or manual certificate paths configured)

### Access Control

- [ ] DNS configured: `MAGPIE_DOMAIN` resolves to server IP address
- [ ] SSL/TLS certificates generated and valid (verify certificate expiration dates)
- [ ] Bearer token authentication enabled (at minimum, one admin token created)
- [ ] IP allowlist configured if restricting access to internal networks (optional, via `MAGPIE_ALLOWED_CIDRS`)
- [ ] Authentik SSO configured if enabling browser-based artifact browsing (optional, see `docs/authentik-setup.md`)

## Configuration & Initialization

### Server Variables

- [ ] `MAGPIE_DOMAIN` set to production domain (e.g., `magpie.example.com`)
- [ ] `MAGPIE_DATA_DIR` set to persistent storage path
- [ ] `MAGPIE_DEBUG` explicitly set to `false` in production
- [ ] `MAGPIE_RETENTION_DAYS` configured appropriately (default: 90 days)
- [ ] Garbage collection (GC) scheduler configured (systemd timer or cron, see `deployment/README.md`)

### Optional Configuration

- [ ] `MAGPIE_SENTRY_DSN` configured if using error tracking (obtain from Sentry)
- [ ] `MAGPIE_OTEL_ENABLED` configured if using OpenTelemetry tracing
- [ ] `AUTHENTIK_HOST` configured if enabling SSO (e.g., `authentik.example.com`)
- [ ] `MAGPIE_ALLOWED_CIDRS` configured if restricting API access by source IP

### Initialization

- [ ] Docker Compose production configuration started: `docker compose -f docker-compose.prod.yml up -d`
- [ ] Storage initialized: `docker compose exec magpie magpie-ctl init` (admin token saved securely)
- [ ] At least one non-admin token created for operational use:
  - Read-only token: `docker compose exec magpie magpie-ctl token create --scope read --name ci-reader`
  - Write token: `docker compose exec magpie magpie-ctl token create --scope write --name ci-deployer`

## Validation

### Health Checks

- [ ] Health endpoint responds: `curl https://magpie.example.com/health`
- [ ] Container health check passes: `docker compose ps` shows magpie service status as "healthy"
- [ ] Caddy reverse proxy healthy: `docker compose ps` shows caddy service running

### Artifact Operations

- [ ] Upload works with token: `magpie push testfile.txt --to test --server https://magpie.example.com --token <write-token>`
- [ ] Download works with token: `magpie get test:latest --server https://magpie.example.com --token <read-token>`
- [ ] Tag operations work: `magpie tag test:latest --as stable --server https://magpie.example.com --token <write-token>`
- [ ] Artifact listing works: `magpie ls test --server https://magpie.example.com --token <read-token>`

### Authentication

- [ ] API returns 401 for missing token: `curl https://magpie.example.com/api/v1/artifacts/test`
- [ ] API returns 200 for valid token: `curl -H "Authorization: Bearer <token>" https://magpie.example.com/api/v1/artifacts/test`
- [ ] Token scopes enforced (write token cannot call admin endpoints):
  - Try admin operation with write token: `magpie token list --token <write-token>` should fail with 403

### TLS & Security

- [ ] HTTPS enforced: HTTP requests redirect to HTTPS (`curl -L http://magpie.example.com/health` follows redirect)
- [ ] TLS certificate valid and not self-signed (check certificate chain)
- [ ] Security headers present in response: `curl -I https://magpie.example.com/` shows `Strict-Transport-Security`, `X-Content-Type-Options`, `X-Frame-Options`
- [ ] HSTS preload header correct: initially 30 days during deployment, increase to 2 years after verification

### Backup & Disaster Recovery

- [ ] Backup strategy documented (see `docs/backup-restore.md`)
- [ ] Initial backup taken and verified restorable from:
  - Storage directory: `$MAGPIE_DATA_DIR/`
  - Database: `$MAGPIE_DATA_DIR/.magpie.db`
- [ ] Test restore procedure documented and practiced

### Garbage Collection

- [ ] GC scheduler installed (systemd timer or cron, see `deployment/README.md`)
- [ ] GC dry-run executed and reviewed: `docker compose exec magpie magpie-ctl gc --dry-run`
- [ ] Lock file path writable by GC process (default: `/var/run/magpie-gc.lock`)
- [ ] GC logs monitored (systemd: `journalctl -u magpie-gc.service`, cron: `/var/log/syslog`)

### Optional: Monitoring & Observability

- [ ] Sentry error tracking verified (if configured, see `docs/sentry-verification.md`)
- [ ] Container logs accessible: `docker compose logs magpie`, `docker compose logs caddy`
- [ ] Log aggregation system connected if required by operations team
- [ ] OpenTelemetry tracing validated if enabled (`MAGPIE_OTEL_ENABLED=true`)

### Optional: Authentik SSO

- [ ] Authentik Forward Auth Provider created with correct configuration
- [ ] Authentik Application created pointing to Magpie domain
- [ ] Access policies/group bindings configured
- [ ] Browser test: visit `https://magpie.example.com/artifacts/`, verify SSO redirect works
- [ ] Token authentication still works (Bearer tokens bypass SSO)

## Pre-Production Acceptance

### Operations

- [ ] All tokens created and distributed securely (admin token rotated, operational tokens provisioned)
- [ ] On-call team trained on basic operations (health checks, GC monitoring, log review)
- [ ] Runbook documented for:
  - Emergency token revocation
  - Manual GC execution
  - Container restart procedure
  - Backup restore procedure

### Documentation

- [ ] Deployment configuration committed to version control (with sensitive values in secrets management)
- [ ] Integration endpoints documented for teams using Magpie
- [ ] Token scope requirements documented for each integration point

## Post-Deployment Verification

### Day 1 Operations

- [ ] Monitor Caddy/Magpie logs for errors during initial load
- [ ] Verify GC runs at scheduled time
- [ ] Confirm backup process executes if automated
- [ ] Test token creation and revocation workflow

### Day 30 Operations

- [ ] Verify HSTS max-age increased to 2 years (production standard)
- [ ] Review GC execution: artifacts eligible for deletion correctly identified
- [ ] Capacity monitoring: storage usage trending as expected
- [ ] Security: Review access logs for anomalies

---

**Note:** This checklist documents operational requirements verified against Magpie's codebase. For developer-facing requirements (code quality, tests), refer to `CONTRIBUTING.md`.

This documentation was created with AI assistance (Claude Code w/ Opus 4.5).
