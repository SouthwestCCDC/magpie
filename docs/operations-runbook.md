# Magpie Operations Runbook

Quick reference guide for deploying, maintaining, troubleshooting, and monitoring Magpie artifact storage in production.

## Table of Contents

1. [Quick Commands](#quick-commands)
2. [Deployment](#deployment)
3. [Common Tasks](#common-tasks)
4. [Troubleshooting](#troubleshooting)
5. [Monitoring & Alerts](#monitoring--alerts)
6. [Disaster Recovery](#disaster-recovery)
7. [Maintenance Windows](#maintenance-windows)
8. [On-Call Playbook](#on-call-playbook)
9. [Automation](#automation)

---

## Quick Commands

```bash
# Service status
docker compose -f docker-compose.prod.yml ps

# View logs
docker compose -f docker-compose.prod.yml logs magpie -f

# Health check
curl https://magpie.example.com/health

# Admin status via magpie CLI (requires server URL + admin token)
# Option 1: Use environment variable (recommended for security)
MAGPIE_TOKEN="your-admin-token-here" magpie --server https://magpie.example.com status

# Option 2: Configure once, use repeatedly (see [user-guide.md#configuration](user-guide.md#configuration))
# magpie config --server https://magpie.example.com --token your-admin-token-here
# magpie status

# Restart service
docker compose -f docker-compose.prod.yml restart magpie

# Stop for maintenance
docker compose -f docker-compose.prod.yml stop
docker compose -f docker-compose.prod.yml start

# Emergency: reset admin token
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl init --reset-admin-token
```

---

## Deployment

### Initial Setup

Follow [Installation Guide](installation.md) for:
1. Docker Compose setup
2. Persistent storage configuration
3. TLS/HTTPS setup

### Pre-Deployment Checklist

See [Production Checklist](production-checklist.md) for:
- DNS and firewall configuration
- Security settings
- Deployment validation steps

### Docker Compose Environment Variables

| Variable | Required | Example |
|----------|----------|---------|
| `MAGPIE_DATA_DIR` | No | `/mnt/nfs/magpie` (default: `./data`) |
| `MAGPIE_DOMAIN` | Yes (prod) | `magpie.example.com` |

These are used by `docker-compose.prod.yml` for volume mounting and Caddy configuration.
The Magpie application does not read these variables.

### Application Environment Variables

| Variable | Required | Example |
|----------|----------|---------|
| `MAGPIE_RETENTION_DAYS` | No | `90` (default) |
| `MAGPIE_DEBUG` | No | `false` (always false in production) |

Full reference: [User Guide - Server Environment Variables](user-guide.md#server-environment-variables)


---

## Common Tasks

### Token Management

**List all tokens:**
```bash
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl token list
```

**Create a new token:**
```bash
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl token create \
  --name ci-deployer \
  --scope write
```

Available scopes: `read`, `write`, `admin`

**Revoke a token:**
```bash
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl token revoke <token-name>
```

**Rotate admin token (security best practice):**
1. Create new admin token:
   ```bash
   docker compose -f docker-compose.prod.yml exec magpie magpie-ctl token create --name ops-admin-new --scope admin
   ```
2. Revoke old token:
   ```bash
   docker compose -f docker-compose.prod.yml exec magpie magpie-ctl token revoke ops-admin
   ```

### Artifact Management

**Push an artifact:**
```bash
magpie push /path/to/file --to team/artifact-name
```

**List artifacts:**
```bash
magpie ls team/
```

**Download artifact:**
```bash
magpie get team/artifact-name:latest
```

**Create a tag (point to different version):**
```bash
magpie tag team/artifact-name:v1.0.0 --as latest
```

**Remove a tag:**
```bash
magpie untag team/artifact-name:latest
```

**View artifact metadata:**
```bash
magpie info team/artifact-name
```

Full reference: [User Guide - Basic Usage](user-guide.md#basic-usage)

### Garbage Collection

**View pending deletions (dry-run):**
```bash
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl gc --dry-run
```

**Run garbage collection:**
```bash
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl gc
```

**Reconcile symlinks (repair after corruption):**
```bash
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl gc --reconcile-only
```

GC removes untagged blobs older than `MAGPIE_RETENTION_DAYS`. Configure in `.env`:
```bash
MAGPIE_RETENTION_DAYS=90  # Keep untagged artifacts for 90 days
```

Recommended: Run GC on a schedule (e.g., weekly):
```bash
# Crontab entry
0 2 * * 0 cd /path/to/magpie && docker compose -f docker-compose.prod.yml exec -T magpie magpie-ctl gc
```

### Storage Analysis

**Check storage utilization:**
```bash
# Using magpie CLI (requires admin token and server URL)
MAGPIE_TOKEN="your-admin-token-here" magpie --server https://magpie.example.com status
```

Example output (human-readable format, default):
```
Server:    https://magpie.example.com
Status:    OK
Version:   0.1.0
Storage:   1.15 GB used
Artifacts: 42 total
Blobs:     156 total
```

**JSON output** (for scripting/monitoring):
```bash
MAGPIE_TOKEN="your-admin-token-here" magpie --server https://magpie.example.com status --format json
```

Example JSON output:
```json
{
  "server": "https://magpie.example.com",
  "status": "ok",
  "version": "0.1.0",
  "storage": {
    "total_size_bytes": 1234567890,
    "artifact_count": 42,
    "blob_count": 156
  }
}
```

**List disk usage by artifact:**
```bash
du -sh /path/to/MAGPIE_DATA_DIR/artifacts/*/blobs/ | sort -h
```

---

## Troubleshooting

### Health Check Failed

**Symptom:** `curl https://magpie.example.com/health` returns error or non-200 status

**Check container status:**
```bash
docker compose -f docker-compose.prod.yml ps
```

**View recent logs:**
```bash
docker compose -f docker-compose.prod.yml logs magpie --tail 100
```

**Common causes:**
1. **Port not exposed:** Verify firewall and docker-compose binding
2. **Storage mounted incorrectly:** Check `MAGPIE_DATA_DIR` permissions
3. **Database corrupted:** See [Database Recovery](#database-recovery)

### Upload Failures

**Symptom:** `magpie push` fails or hangs

**Check available disk space:**
```bash
df -h /path/to/MAGPIE_DATA_DIR
```

**Verify temp directory:** Ensure `.tmp` is writable:
```bash
ls -la /path/to/MAGPIE_DATA_DIR/artifacts/.tmp/
```

**Check upload size limits:**
```bash
# Navigate to the docker-compose directory first
cd /path/to/magpie-deployment

# Check via environment/compose config
grep '^MAGPIE_MAX_UPLOAD_SIZE' .env
# Or inspect rendered compose config:
docker compose -f docker-compose.prod.yml config | grep MAGPIE_MAX_UPLOAD_SIZE

# Increase if needed (edit .env in the docker-compose directory)
MAGPIE_MAX_UPLOAD_SIZE=10737418240  # 10GB
```

**Check auth token validity:**
```bash
# Use an environment variable so the token is not exposed in `ps` output
MAGPIE_TOKEN="your-token-here" magpie --server https://magpie.example.com status
```

**Where to get tokens:**
- Admin token: Created during `magpie-ctl init` (see "Token Management" section)
- User tokens: Created via `magpie-ctl token create` (see "Token Management" section)

### Symlink Corruption

**Symptom:** Downloads fail with "not found" errors despite tag existing

**Find broken symlinks:**
```bash
find /path/to/MAGPIE_DATA_DIR/artifacts -type l ! -exec test -e {} \; -print
```

**Repair symlinks:**
```bash
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl gc --reconcile-only
```

### Permission Issues

**Symptom:** "Permission denied" accessing files or database

**Check ownership:**
```bash
ls -la /path/to/MAGPIE_DATA_DIR
```

**Fix permissions (container runs as uid:gid from volume owner):**
```bash
sudo chown -R "${UID}:${GID}" /path/to/MAGPIE_DATA_DIR
chmod -R u+rwX,g+rX,o-rwx /path/to/MAGPIE_DATA_DIR
```

### Database Recovery

**Symptom:** Database locked or corrupted errors in logs

**Stop service:**
```bash
docker compose -f docker-compose.prod.yml stop magpie
```

**Check database integrity:**
```bash
sqlite3 /path/to/MAGPIE_DATA_DIR/magpie.db "PRAGMA integrity_check;"
```

**Truncate WAL (if stuck):**
```bash
sqlite3 /path/to/MAGPIE_DATA_DIR/magpie.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

**Restart service:**
```bash
docker compose -f docker-compose.prod.yml start magpie
```

If database is corrupted, restore from backup (see [Disaster Recovery](#disaster-recovery)).

### S3 Sync Issues

**Symptom:** `magpie-ctl sync to-s3` fails

**Verify AWS credentials:**
```bash
docker compose -f docker-compose.prod.yml exec magpie aws sts get-caller-identity
```

**Test S3 access:**
```bash
docker compose -f docker-compose.prod.yml exec magpie \
  aws s3 ls s3://$MAGPIE_S3_BUCKET --region $AWS_DEFAULT_REGION
```

**Run with dry-run to preview:**
```bash
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl sync to-s3 --dry-run
```

---

## Monitoring & Alerts

See [Monitoring Guide](monitoring.md) for detailed setup. Quick reference:

### Health Check

**Public endpoint** (no auth required):
```bash
curl https://magpie.example.com/health
# Returns: {"status": "ok", "version": "x.y.z"}
```

### Logging

**Check log format (default: JSON):**
```bash
docker compose -f docker-compose.prod.yml logs magpie | head -20
```

**Search for errors:**
```bash
docker compose -f docker-compose.prod.yml logs magpie | grep '"level":"error"'
```

**Enable verbose logging (development only):**
```bash
export MAGPIE_DEBUG=true
docker compose -f docker-compose.prod.yml up -d
```

### Critical Alerts (set up monitoring for these)

| Condition | Severity | Action |
|-----------|----------|--------|
| Health endpoint returns non-200 | Critical | Page on-call |
| Disk space < 20% free | Warning | Schedule cleanup/expansion |
| Error rate > 5% of requests | Critical | Check logs, review recent changes |
| Request latency p95 > 5s | Warning | Check storage I/O, disk space |
| 3+ consecutive health check failures | Critical | Investigate storage and network |

### Log Aggregation

Magpie outputs JSON logs suitable for Loki, Datadog, or Elasticsearch:

```json
{
  "timestamp": "2026-01-17T00:29:09.772480Z",
  "level": "info",
  "event": "upload_complete",
  "artifact_path": "images/ubuntu",
  "hash_ref": "abc12345",
  "size_bytes": 51200,
  "duration_ms": 45.12,
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

Set log format in `.env`:
```bash
MAGPIE_LOG_FORMAT=json    # Machine-readable (production)
MAGPIE_LOG_FORMAT=console # Human-readable (dev only)
```

### Optional: Sentry Integration

For error tracking and performance monitoring:

1. Create [Sentry](https://sentry.io) account
2. Get DSN from project settings
3. Set environment variable:
   ```bash
   MAGPIE_SENTRY_DSN="https://<key>@<org>.ingest.sentry.io/<project>"
   ```
4. Restart service

Sentry captures 5xx errors and optional performance traces (10% sampling in production).

---

## Disaster Recovery

### Backup Strategy

**Automated backups** (set up with cron):
```bash
# Daily full backup script
MAGPIE_DATA_DIR="${MAGPIE_DATA_DIR:-./data}"
BACKUP_PATH="/backups/magpie/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_PATH"

# Backup artifacts (exclude temp files)
rsync -av --exclude='.tmp/' "$MAGPIE_DATA_DIR/artifacts/" "$BACKUP_PATH/artifacts/"

# Backup token database
sqlite3 "$MAGPIE_DATA_DIR/magpie.db" ".backup '$BACKUP_PATH/magpie.db'"
```

**Cloud backup** (optional, S3):
```bash
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl sync to-s3
```

Full backup strategy: See [Backup & Restore Guide](backup-restore.md)

### Full Restore Procedure

**1. Stop the service:**
```bash
docker compose -f docker-compose.prod.yml down
```

**2. Restore from backup:**
```bash
BACKUP_PATH="/backups/magpie/20260115-103000"
MAGPIE_DATA_DIR="${MAGPIE_DATA_DIR:-./data}"

mkdir -p "$MAGPIE_DATA_DIR"

# WARNING: --delete will remove files not in backup. Run with --dry-run first!
rsync -av --delete --dry-run "$BACKUP_PATH/artifacts/" "$MAGPIE_DATA_DIR/artifacts/"
# Verify the dry-run output, then run without --dry-run:
rsync -av --delete "$BACKUP_PATH/artifacts/" "$MAGPIE_DATA_DIR/artifacts/"

cp "$BACKUP_PATH/magpie.db" "$MAGPIE_DATA_DIR/magpie.db"
```

**3. Fix permissions:**
```bash
OWNER_UID=$(stat -c %u "$MAGPIE_DATA_DIR")
OWNER_GID=$(stat -c %g "$MAGPIE_DATA_DIR")
chown -R "$OWNER_UID:$OWNER_GID" "$MAGPIE_DATA_DIR"
```

**4. Start service:**
```bash
docker compose -f docker-compose.prod.yml up -d && sleep 5
```

**5. Reconcile symlinks:**
```bash
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl gc --reconcile-only
```

**6. Verify:**
```bash
curl https://magpie.example.com/health
magpie ls team/  # Verify artifacts are accessible
```

### Token Database Loss

If token database is corrupted or lost:

**1. Reset admin token:**
```bash
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl init --reset-admin-token
```

**2. Create new tokens:**
```bash
docker compose -f docker-compose.prod.yml exec magpie \
  magpie-ctl token create --name ci-deployer --scope write
```

**3. Distribute tokens to CI systems**

### Partial Recovery (Tokens Only)

If you have a backup of tokens but artifacts are fine:

```bash
docker compose -f docker-compose.prod.yml stop magpie

# Restore token database
cp "$BACKUP_PATH/magpie.db" "$MAGPIE_DATA_DIR/magpie.db"

# Fix database state
sqlite3 "$MAGPIE_DATA_DIR/magpie.db" "PRAGMA wal_checkpoint(TRUNCATE);"

# Fix permissions
chown "$(stat -c %u:%g "$MAGPIE_DATA_DIR")" "$MAGPIE_DATA_DIR/magpie.db"

docker compose -f docker-compose.prod.yml start magpie
```

---

## Maintenance Windows

### Planned Downtime

**Notify users:**
```bash
# Post message in relevant chat channels about upcoming maintenance
```

**Schedule during off-peak hours:**
- Avoid build times (check CI schedules)
- Allow 30+ minutes for patching and testing

**Procedure:**
```bash
# 1. Stop the service
docker compose -f docker-compose.prod.yml stop magpie

# 2. Perform maintenance (OS patching, dependency updates, etc.)

# 3. Test
docker compose -f docker-compose.prod.yml up -d
sleep 10
curl https://magpie.example.com/health

# 4. Verify core functionality
magpie ls team/   # List artifacts
```

### Version Upgrade

**Before upgrading:**
1. Back up database and artifacts
2. Review release notes for breaking changes
3. Test in staging if available

**Upgrade procedure:**
```bash
# Pull new image
docker compose -f docker-compose.prod.yml pull

# Restart with new version
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml up -d

# Check logs for errors
docker compose -f docker-compose.prod.yml logs magpie -f --tail 50
```

**Rollback procedure** (if upgrade fails):
```bash
# 1. Stop the service
docker compose -f docker-compose.prod.yml down

# 2. Identify the previous image version
docker images | grep magpie

# 3. Edit docker-compose.prod.yml to pin the previous version
# Change: image: ghcr.io/southwestccdc/magpie:latest
# To:     image: ghcr.io/southwestccdc/magpie:v0.X.Y

# 4. Start with the previous version
docker compose -f docker-compose.prod.yml up -d

# 5. Verify rollback succeeded
curl https://magpie.example.com/health
docker compose -f docker-compose.prod.yml logs magpie --tail 50

# 6. If database migration ran during failed upgrade, restore from backup
# (See "Full Restore Procedure" in Disaster Recovery section)
```

---

## On-Call Playbook

**What to check first:**
1. Health endpoint: `curl https://magpie.example.com/health`
2. Recent logs: `docker compose -f docker-compose.prod.yml logs magpie --tail 100`
3. Disk space: `df -h /path/to/MAGPIE_DATA_DIR`
4. Container status: `docker compose -f docker-compose.prod.yml ps`

**Escalation path:**
- Check [Troubleshooting](#troubleshooting) section
- Review [Monitoring Guide](monitoring.md)
- Contact platform team if infrastructure issue (networking, storage, host)
- Reference [User Guide](user-guide.md) for concepts and architecture questions

**Common quick fixes:**
- Restart service: `docker compose -f docker-compose.prod.yml restart magpie`
- Repair symlinks: `docker compose -f docker-compose.prod.yml exec magpie magpie-ctl gc --reconcile-only`
- Clear temp files: `rm -rf /path/to/MAGPIE_DATA_DIR/artifacts/.tmp/*`

---

## Automation

### Scheduled Tasks (Cron Examples)

**Daily garbage collection** (remove old untagged artifacts):
```bash
# Add to crontab (crontab -e)
# Run GC every day at 2 AM
0 2 * * * cd /path/to/magpie && docker compose -f docker-compose.prod.yml exec -T magpie magpie-ctl gc >> /var/log/magpie-gc.log 2>&1
```

**Weekly backups**:
```bash
# Run full backup every Sunday at 3 AM
0 3 * * 0 /usr/local/bin/magpie-backup.sh >> /var/log/magpie-backup.log 2>&1
```

Example backup script (`/usr/local/bin/magpie-backup.sh`):
```bash
#!/bin/bash
set -euo pipefail

MAGPIE_DATA_DIR="${MAGPIE_DATA_DIR:-/var/lib/magpie}"
BACKUP_ROOT="/backups/magpie"
BACKUP_PATH="$BACKUP_ROOT/$(date +%Y%m%d-%H%M%S)"
RETENTION_DAYS=30

mkdir -p "$BACKUP_PATH"

# Backup artifacts (exclude temp files)
rsync -av --exclude='.tmp/' "$MAGPIE_DATA_DIR/artifacts/" "$BACKUP_PATH/artifacts/"

# Backup token database
sqlite3 "$MAGPIE_DATA_DIR/magpie.db" ".backup '$BACKUP_PATH/magpie.db'"

# Clean up old backups
find "$BACKUP_ROOT" -maxdepth 1 -type d -mtime +$RETENTION_DAYS -exec rm -rf {} \;

echo "Backup completed: $BACKUP_PATH"
```

**Health check monitoring** (every 5 minutes):
```bash
# Add to crontab
*/5 * * * * curl -sf https://magpie.example.com/health || echo "Magpie health check failed at $(date)" | mail -s "ALERT: Magpie Down" ops@example.com
```

**Disk space monitoring** (hourly):
```bash
# Add to crontab
0 * * * * /usr/local/bin/magpie-disk-check.sh
```

Example disk check script (`/usr/local/bin/magpie-disk-check.sh`):
```bash
#!/bin/bash
MAGPIE_DATA_DIR="${MAGPIE_DATA_DIR:-/var/lib/magpie}"
THRESHOLD=80  # Alert if usage exceeds 80%

USAGE=$(df -h "$MAGPIE_DATA_DIR" | awk 'NR==2 {print $5}' | sed 's/%//')

if [ "$USAGE" -gt "$THRESHOLD" ]; then
    echo "WARNING: Magpie storage at ${USAGE}% capacity" | \
        mail -s "ALERT: Magpie Disk Space Low" ops@example.com
fi
```

### Ansible Integration

For automating Magpie operations in Ansible playbooks, see [Ansible Integration Guide](ansible-integration.md).

**Common playbook tasks:**
- Token creation and distribution
- Artifact deployment during provisioning
- Health check verification in deployment pipelines
- Automated backups with vault integration

**Example playbook snippet:**
```yaml
- name: Run garbage collection
  community.docker.docker_container_exec:
    container: magpie
    command: magpie-ctl gc
  when: inventory_hostname == groups['magpie_server'][0]

- name: Verify magpie health
  uri:
    url: "https://{{ magpie_domain }}/health"
    status_code: 200
  register: health_check
  retries: 3
  delay: 5
```

---

## Related Documentation

- [Installation Guide](installation.md) - Initial setup
- [User Guide](user-guide.md) - Client CLI reference and configuration
- [Production Checklist](production-checklist.md) - Pre-deployment verification
- [Backup & Restore](backup-restore.md) - Detailed backup procedures
- [Monitoring Guide](monitoring.md) - Health checks, logging, observability setup
- [Ansible Integration](ansible-integration.md) - Using Magpie in playbooks

---

*(AI-generated via Claude Code w/ Opus 4.5)*
