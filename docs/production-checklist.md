# Magpie Production Readiness Checklist

This checklist ensures you've configured all required components before deploying Magpie to production.

## Pre-Deployment Requirements

### Hardware and Storage

- [ ] Verify storage capacity meets artifact volume requirements
  - Recommended: 1TB+ for base images, game assets, and build artifacts
  - Consider future growth (3-6 months of projected uploads)
- [ ] Confirm disk performance acceptable for concurrent uploads/downloads
  - SSD strongly recommended for primary storage
  - HDD acceptable for backup/archive storage
- [ ] Mount artifact storage at persistent location
  - Default: `/data/artifacts` (configurable via `MAGPIE_DATA_DIR`)
  - Ensure mount survives reboots (check `/etc/fstab`)
  - Verify write permissions for container user (UID 1000 by default)

### DNS and TLS

- [ ] Configure DNS A/AAAA record for Magpie domain
  - Record must point to server IP before deployment
  - Verify propagation: `dig +short magpie.example.com`
- [ ] Ensure ports 80 and 443 are accessible
  - Port 80 required for Let's Encrypt HTTP-01 challenge
  - Port 443 required for HTTPS traffic
  - Test with `nc -zv <server-ip> 443`
- [ ] Confirm no other services using ports 80/443
  - Check: `sudo lsof -i :80` and `sudo lsof -i :443`
  - Stop conflicting services or use alternate ports via `MAGPIE_HTTP_PORT`/`MAGPIE_HTTPS_PORT`
- [ ] Decide TLS strategy:
  - **Let's Encrypt (recommended):** Automatic via Caddy (default configuration)
  - **Manual certificates:** Uncomment and configure TLS block in `Caddyfile.prod`
  - **Internal CA:** Use `tls internal` for testing/development environments

### Firewall Configuration

- [ ] Open inbound ports in firewall:
  - TCP 80 (HTTP, redirects to HTTPS)
  - TCP 443 (HTTPS)
- [ ] Configure IP allow-list if needed (optional):
  - Set `MAGPIE_ALLOWED_CIDRS` for trusted networks (e.g., `10.0.0.0/8,192.168.1.0/24`)
  - Allows bypassing bearer token auth from trusted IPs
  - See `Caddyfile.prod` lines 98-112 for security implications

### Backup Storage

- [ ] Plan backup strategy (see [backup-restore.md](backup-restore.md)):
  - **3-2-1 rule:** 3 copies, 2 media types, 1 offsite
  - Recommended frequency: Daily full or incremental backups
- [ ] Provision backup storage location:
  - Network storage (NFS/SMB), S3-compatible storage, or local disk
  - Verify sufficient capacity (backup size will match artifact storage)
- [ ] Schedule automated backups (systemd timer or cron):
  - See [backup-restore.md](backup-restore.md#automation) for examples
  - Test restore procedure before production use

## Configuration

### Required Environment Variables

- [ ] Set `MAGPIE_DOMAIN` (REQUIRED):
  ```bash
  MAGPIE_DOMAIN=magpie.example.com
  ```
  - Let's Encrypt will fail without a valid, resolvable domain
  - Must match DNS A/AAAA record exactly
- [ ] Set `MAGPIE_DATA_DIR` (default: `./data/artifacts`):
  ```bash
  MAGPIE_DATA_DIR=/data/artifacts
  ```
  - Absolute path to persistent storage mount
  - Must be writable by container user (UID 1000)

### Optional Environment Variables

- [ ] Configure `MAGPIE_HTTP_PORT` / `MAGPIE_HTTPS_PORT` if non-standard:
  ```bash
  MAGPIE_HTTP_PORT=8080
  MAGPIE_HTTPS_PORT=8443
  ```
  - Default: 80 and 443
  - Adjust if ports are already in use
- [ ] Configure `MAGPIE_ALLOWED_CIDRS` for IP allow-listing (optional):
  ```bash
  MAGPIE_ALLOWED_CIDRS=10.0.0.0/8,192.168.1.0/24
  ```
  - Comma-separated CIDR ranges
  - Bypasses bearer token authentication for trusted networks
  - **Security warning:** Only add trusted internal networks
- [ ] Configure `AUTHENTIK_HOST` for SSO (optional):
  ```bash
  AUTHENTIK_HOST=authentik.example.com
  ```
  - Enables browser-based SSO for `/artifacts/*` browsing
  - Requires Authentik provider setup (see [authentik-setup.md](authentik-setup.md))
  - Does not affect API/CLI bearer token authentication
- [ ] Configure `MAGPIE_DEBUG` (should be `false` in production):
  ```bash
  MAGPIE_DEBUG=false
  ```
  - **Critical:** Debug mode exposes stack traces and sensitive error details
  - Only enable temporarily for troubleshooting

### Token Management

- [ ] Initialize admin token on first deployment:
  ```bash
  docker compose exec magpie magpie-ctl init --reset-admin-token
  ```
  - Save admin token securely (password manager, secrets vault)
  - Admin token shown only once during initialization
- [ ] Create service tokens for automation:
  ```bash
  # CI/CD write token
  docker compose exec magpie magpie-ctl token create --name ci-bot --scope write

  # Read-only token for monitoring/downloads
  docker compose exec magpie magpie-ctl token create --name viewer --scope read
  ```
  - Document token names and scopes in operations runbook
  - Distribute tokens to authorized systems (CI/CD, ops container, Ansible)
- [ ] Store tokens securely:
  - Use Ansible Vault for playbook automation
  - Use CI/CD secrets management (GitHub Secrets, GitLab CI variables)
  - Never commit tokens to version control

### Garbage Collection

- [ ] Configure GC retention policy (default: 30 days):
  - Untagged blobs older than retention period are eligible for deletion
  - Adjust retention based on disk capacity and usage patterns
  - Run manual GC to test: `docker compose exec magpie magpie-ctl gc --dry-run`
- [ ] Schedule automated garbage collection (optional):
  ```bash
  # Example: Daily GC via systemd timer or cron
  0 3 * * * docker compose exec magpie magpie-ctl gc --retention-days 30
  ```
  - See `magpie-ctl gc --help` for options
  - Use `--dry-run` first to preview deletions
- [ ] Verify symlink reconciliation on first GC run:
  ```bash
  docker compose exec magpie magpie-ctl gc --reconcile-only
  ```
  - Reconciles symlinks to match manifest state
  - Safe to run anytime (does not delete blobs)

### Logging and Observability

- [ ] Configure log aggregation:
  - Caddy logs JSON to stdout (captured by Docker)
  - FastAPI logs structured JSON to stdout
  - Forward logs to centralized logging system (e.g., Loki, ELK, Splunk)
- [ ] Configure Sentry integration (optional):
  - See [sentry-verification.md](sentry-verification.md) for setup
  - Set `SENTRY_DSN` and `SENTRY_ENVIRONMENT` environment variables
  - Captures application errors and performance metrics
- [ ] Enable OpenTelemetry tracing (optional):
  - Set `OTEL_EXPORTER_OTLP_ENDPOINT` for trace export
  - Useful for distributed tracing and performance analysis

## Validation

### Health and Connectivity

- [ ] Verify services start successfully:
  ```bash
  docker compose -f docker-compose.prod.yml up -d
  docker compose ps
  ```
  - Both `caddy` and `magpie` should show `Up (healthy)` status
- [ ] Check health endpoint responds:
  ```bash
  curl https://magpie.example.com/health
  ```
  - Expected: `{"status":"healthy"}`
- [ ] Verify TLS certificate is valid:
  ```bash
  curl -vI https://magpie.example.com 2>&1 | grep -E 'SSL|subject|issuer'
  ```
  - Let's Encrypt: `issuer: C=US; O=Let's Encrypt`
  - Check expiration: `openssl s_client -connect magpie.example.com:443 2>/dev/null | openssl x509 -noout -dates`
- [ ] Confirm HSTS header present:
  ```bash
  curl -sI https://magpie.example.com/health | grep -i strict-transport-security
  ```
  - Expected: `Strict-Transport-Security: max-age=2592000; includeSubDomains`
  - After confirming TLS works, increase max-age to 2 years (63072000) in `Caddyfile.prod`

### Upload and Download

- [ ] Test artifact upload with bearer token:
  ```bash
  # Create test file
  echo "test content" > test-artifact.txt

  # Upload
  magpie push test-artifact.txt --to test/validation

  # Or with curl
  curl -H "Authorization: Bearer <admin-token>" \
    -F "file=@test-artifact.txt" \
    https://magpie.example.com/api/v1/upload/test/validation
  ```
  - Expected: JSON response with hash reference (e.g., `{"hash_ref":"@abc12345"}`)
- [ ] Test download by tag:
  ```bash
  magpie get test/validation:latest

  # Or with curl
  curl -o downloaded.txt \
    https://magpie.example.com/artifacts/test/validation/latest
  ```
  - Verify content: `sha256sum downloaded.txt`
- [ ] Test download by hash reference:
  ```bash
  curl -o downloaded.txt \
    https://magpie.example.com/artifacts/test/validation/blobs/abc12345
  ```
  - Should match upload hash

### Authentication and Authorization

- [ ] Verify bearer token authentication works:
  ```bash
  # Valid token (should succeed)
  curl -H "Authorization: Bearer <valid-token>" \
    https://magpie.example.com/api/v1/artifacts

  # Invalid token (should return 401)
  curl -H "Authorization: Bearer invalid-token" \
    https://magpie.example.com/api/v1/artifacts
  ```
  - Valid token: HTTP 200 with artifact list
  - Invalid token: HTTP 401 Unauthorized
- [ ] Verify token scope enforcement:
  ```bash
  # Read token cannot upload (should return 403)
  curl -H "Authorization: Bearer <read-token>" \
    -F "file=@test.txt" \
    https://magpie.example.com/api/v1/upload/test/scope-test
  ```
  - Expected: HTTP 403 Forbidden
- [ ] Test unauthenticated access to public artifacts:
  ```bash
  curl https://magpie.example.com/artifacts/public/test.txt
  ```
  - Should succeed without bearer token
  - Files in `/artifacts/public/*` are always accessible

### Backup and Restore

- [ ] Run initial backup:
  ```bash
  # Example using rsync
  rsync -av --exclude='.tmp/' /data/artifacts/ /backup/magpie/initial/
  ```
  - Verify backup contains `.magpie` manifests and `blobs/*` files
  - Check database backup: `ls -lh /backup/magpie/initial/.magpie.db`
- [ ] Test non-destructive restore in isolated environment:
  ```bash
  # Restore to temporary location
  rsync -av /backup/magpie/initial/ /tmp/magpie-restore-test/

  # Verify manifests are valid JSON
  find /tmp/magpie-restore-test -name ".magpie" -exec jq . {} \;
  ```
  - See [backup-restore.md](backup-restore.md#verification) for full test procedure
- [ ] Document restore procedure and RTO targets:
  - Record tested restore time for your storage size
  - Update operations runbook with backup locations and access procedures
  - Test quarterly to ensure backups remain viable

## Security

### HTTPS and Encryption

- [ ] Confirm HTTP redirects to HTTPS:
  ```bash
  curl -I http://magpie.example.com
  ```
  - Expected: HTTP 301 or 308 redirect to `https://`
- [ ] Verify security headers present:
  ```bash
  curl -sI https://magpie.example.com/health | grep -E 'Strict-Transport|X-Content-Type|X-Frame|Content-Security'
  ```
  - Should include: HSTS, X-Content-Type-Options, X-Frame-Options, CSP
- [ ] Test for TLS vulnerabilities (optional):
  ```bash
  # Using testssl.sh
  ./testssl.sh https://magpie.example.com
  ```
  - Should show no critical vulnerabilities
  - Let's Encrypt provides modern TLS 1.2+ by default

### IP Allow-Listing

- [ ] If using `MAGPIE_ALLOWED_CIDRS`, verify IP exemptions work:
  ```bash
  # From allowed IP (should succeed without token)
  curl https://magpie.example.com/api/v1/artifacts

  # From non-allowed IP (should require token)
  curl https://magpie.example.com/api/v1/artifacts
  ```
  - Allowed IP: HTTP 200 without Authorization header
  - Non-allowed IP: HTTP 401 Unauthorized
- [ ] Document allowed CIDR ranges in operations runbook:
  - Why each range is allowed
  - Who approved the exemption
  - Review quarterly for stale entries

### Token Scopes and Lifecycle

- [ ] Verify admin scope required for sensitive operations:
  ```bash
  # Non-admin token attempting token creation (should fail)
  curl -H "Authorization: Bearer <write-token>" \
    -X POST https://magpie.example.com/api/v1/tokens \
    -H "Content-Type: application/json" \
    -d '{"name":"unauthorized","scope":"write"}'
  ```
  - Expected: HTTP 403 Forbidden
- [ ] Document token rotation policy:
  - Recommended: Rotate service tokens every 90 days
  - Rotate immediately if token is compromised
  - Use `magpie-ctl token revoke` to invalidate old tokens
- [ ] Plan post-deployment token distribution:
  - Update CI/CD pipelines with new tokens
  - Distribute tokens to authorized team members
  - Configure Ansible playbooks with vault-encrypted tokens

## Post-Deployment

### Monitoring

- [ ] Set up health check monitoring:
  - Poll `/health` endpoint every 60 seconds
  - Alert on non-200 responses or timeout
- [ ] Monitor disk usage:
  ```bash
  df -h /data/artifacts
  ```
  - Alert at 80% capacity
  - Plan storage expansion or increase GC frequency
- [ ] Monitor Caddy and FastAPI logs for errors:
  ```bash
  docker compose logs -f caddy magpie
  ```
  - Forward to centralized logging system
  - Alert on HTTP 5xx errors or auth failures

### Documentation

- [ ] Document deployment-specific details:
  - Server IP and hostname
  - Backup locations and schedule
  - Token names and scopes (not token values)
  - IP allow-list ranges (if configured)
  - Retention policy settings
- [ ] Create operations runbook with:
  - Restart procedures
  - Backup and restore steps
  - Token rotation procedures
  - Emergency contact information
  - Tested RTO/RPO metrics

### Team Onboarding

- [ ] Distribute client configuration template:
  ```toml
  # ~/.magpie/config.toml
  [client]
  server = "https://magpie.example.com"
  token = "mgp_your_token_here"
  ```
  - Provide individual tokens (not shared admin token)
  - Document in team wiki or internal docs
- [ ] Share user guide and CLI examples:
  - Link to [user-guide.md](user-guide.md)
  - Common workflows: upload, download, tag management
- [ ] Configure Ansible integration (if applicable):
  - See [ansible-integration.md](ansible-integration.md)
  - Distribute vault-encrypted tokens for automation

## Additional Resources

- [User Guide](user-guide.md) - CLI usage and workflows
- [Design Document](design.md) - Architecture and implementation details
- [Backup and Restore Guide](backup-restore.md) - Operational procedures
- [Authentik Setup Guide](authentik-setup.md) - SSO integration for browser access
- [Ansible Integration Guide](ansible-integration.md) - Playbook automation
- [Sentry Verification](sentry-verification.md) - Error tracking setup

---

*This documentation was generated with AI assistance (Claude Code w/ Opus 4.5).*
