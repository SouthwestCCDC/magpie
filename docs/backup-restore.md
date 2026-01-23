# Magpie Backup and Restore Guide

This guide covers operational procedures for backing up and restoring Magpie artifact storage.

## Table of Contents

1. [What to Back Up](#what-to-back-up)
2. [Backup Procedures](#backup-procedures)
3. [Restore Procedures](#restore-procedures)
4. [Recovery Scenarios](#recovery-scenarios)
5. [Automation](#automation)
6. [Verification](#verification)

---

## What to Back Up

Magpie stores data in three primary locations that must be backed up:

| Component | Default Location | Description | Criticality |
|-----------|-----------------|-------------|-------------|
| **Storage directory** | `/data/artifacts/` | Blob files, manifests, symlinks | Critical |
| **Metadata directory** | `/data/artifacts/*/metadata/` | Blob provenance (uploader, timestamps) | High |
| **SQLite database** | `/data/artifacts/.magpie.db` | Authentication tokens | High |
| **Configuration files** | Container environment/`.env` | Server settings | Medium |

### Storage Directory Structure

```
/data/artifacts/
├── .magpie.db              # SQLite database (tokens)
├── .tmp/                   # Temporary upload staging
├── {artifact-path}/        # Artifact directories (e.g., images/ubuntu/)
│   ├── .magpie             # Manifest JSON (tags)
│   ├── blobs/              # Content-addressed blob storage
│   │   └── {hash}          # Actual artifact files (8-char SHA-256 prefix)
│   ├── metadata/           # Blob provenance sidecars
│   │   └── {hash}.json     # Uploader, timestamps, source URI
│   ├── latest -> blobs/{hash}  # Symlinks for tags
│   ├── stable -> blobs/{hash}
│   └── v1.0 -> blobs/{hash}
```

### What NOT to Back Up

- **`.tmp/` directory** - Temporary upload staging, can be recreated
- **Symlinks** - Derived from `.magpie` manifests, automatically reconciled

### Backup Priority Levels

**Critical (must back up):**
- Blob files (`*/blobs/*`) - The actual artifact data
- Manifest files (`*/.magpie`) - Tag definitions and metadata

**High (strongly recommended):**
- SQLite database (`.magpie.db`) - Token authentication state
- WAL files (`.magpie.db-wal`, `.magpie.db-shm`) - If actively writing

**Medium (recommended):**
- Server configuration (environment variables, `.env` file)
- Caddy configuration (`Caddyfile.prod`)
- Docker Compose configuration (`docker-compose.prod.yml`)

**Low (optional):**
- Symlinks - Can be regenerated from manifests
- Temp directory - Transient upload staging

---

## Backup Procedures

### Manual Backup

#### Full Backup with rsync

The simplest approach for backing up Magpie storage:

```bash
#!/bin/bash
# backup-magpie.sh - Full backup of Magpie storage

STORAGE_PATH="/data/artifacts"
BACKUP_PATH="/backup/magpie/$(date +%Y%m%d-%H%M%S)"
DOCKER_CONTAINER="magpie"  # Adjust if your container name differs

# Create backup directory
mkdir -p "$BACKUP_PATH"

# Stop writes (optional, for consistency)
# docker compose stop magpie

# Backup storage directory (includes blobs, manifests, database)
rsync -av --exclude='.tmp/' "$STORAGE_PATH/" "$BACKUP_PATH/artifacts/"

# If database is actively being written, use SQLite backup
docker compose exec -T "$DOCKER_CONTAINER" \
  sqlite3 /data/artifacts/.magpie.db ".backup '/data/artifacts/.magpie.db.backup'"
docker compose cp "$DOCKER_CONTAINER":/data/artifacts/.magpie.db.backup "$BACKUP_PATH/magpie.db"
docker compose exec -T "$DOCKER_CONTAINER" rm /data/artifacts/.magpie.db.backup

# Backup configuration
cp .env "$BACKUP_PATH/env" 2>/dev/null || true
cp docker-compose.prod.yml "$BACKUP_PATH/" 2>/dev/null || true
cp Caddyfile.prod "$BACKUP_PATH/" 2>/dev/null || true

# Resume writes (if stopped)
# docker compose start magpie

# Record backup metadata
cat > "$BACKUP_PATH/backup-info.txt" <<EOF
Backup Date: $(date -Iseconds)
Magpie Version: $(docker compose exec -T "$DOCKER_CONTAINER" magpie version 2>/dev/null || echo "unknown")
Storage Path: $STORAGE_PATH
Backup Size: $(du -sh "$BACKUP_PATH" | cut -f1)
EOF

echo "Backup completed: $BACKUP_PATH"
echo "Total size: $(du -sh "$BACKUP_PATH" | cut -f1)"
```

Make the script executable:
```bash
chmod +x backup-magpie.sh
```

#### Incremental Backup

For large storage volumes, use incremental backups:

```bash
#!/bin/bash
# backup-magpie-incremental.sh - Incremental backup using rsync

STORAGE_PATH="/data/artifacts"
BACKUP_ROOT="/backup/magpie"
CURRENT="$BACKUP_ROOT/current"
SNAPSHOT="$BACKUP_ROOT/$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_ROOT"

# Create incremental backup with hardlinks to previous backup
rsync -av --exclude='.tmp/' \
  --link-dest="$CURRENT/artifacts/" \
  "$STORAGE_PATH/" "$SNAPSHOT/artifacts/"

# Update current symlink
rm -f "$CURRENT"
ln -s "$(basename "$SNAPSHOT")" "$CURRENT"

echo "Incremental backup completed: $SNAPSHOT"
```

This approach saves space by hardlinking unchanged files.

#### Database-Only Backup

To back up only the token database (fast, for frequent snapshots):

```bash
#!/bin/bash
# backup-magpie-db.sh - Database-only backup

BACKUP_PATH="/backup/magpie-db/$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_PATH"

# SQLite online backup (safe during writes)
# Note: Backup to container temp dir first, then copy to host
docker compose exec -T magpie \
  sqlite3 /data/artifacts/.magpie.db ".backup '/tmp/magpie.db'"
docker compose cp magpie:/tmp/magpie.db "$BACKUP_PATH/magpie.db"
docker compose exec -T magpie rm /tmp/magpie.db

echo "Database backup completed: $BACKUP_PATH/magpie.db"
```

### Remote Backup

#### To Network Storage

```bash
# Backup to NFS/SMB mount
rsync -av --exclude='.tmp/' \
  /data/artifacts/ /mnt/nas/magpie-backup/artifacts/
```

#### To S3-Compatible Storage

```bash
# Using aws-cli
aws s3 sync /data/artifacts/ s3://magpie-backup/artifacts/ \
  --exclude '.tmp/*' \
  --storage-class STANDARD_IA

# Using rclone (supports many providers)
rclone sync /data/artifacts/ remote:magpie-backup/artifacts/ \
  --exclude '.tmp/**'
```

---

## Restore Procedures

### Full Restore

To restore from a complete backup:

```bash
#!/bin/bash
# restore-magpie.sh - Full restore from backup

BACKUP_PATH="/backup/magpie/20260115-103000"  # Adjust to your backup
STORAGE_PATH="/data/artifacts"

# Stop Magpie services
docker compose down

# Verify backup exists
if [ ! -d "$BACKUP_PATH/artifacts" ]; then
  echo "Error: Backup not found at $BACKUP_PATH"
  exit 1
fi

# Clear existing storage (DANGER: data loss if wrong path!)
read -p "This will DELETE all data in $STORAGE_PATH. Continue? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
  echo "Restore cancelled."
  exit 0
fi

# Backup existing data (safety)
if [ -d "$STORAGE_PATH" ]; then
  mv "$STORAGE_PATH" "$STORAGE_PATH.old.$(date +%s)"
fi

# Restore storage directory
mkdir -p "$STORAGE_PATH"
rsync -av "$BACKUP_PATH/artifacts/" "$STORAGE_PATH/"

# Restore configuration
cp "$BACKUP_PATH/env" .env 2>/dev/null || true

# Set correct permissions
chown -R 1000:1000 "$STORAGE_PATH"  # Adjust UID/GID if needed
chmod 755 "$STORAGE_PATH"

# Recreate temp directory
mkdir -p "$STORAGE_PATH/.tmp"

# Start services
docker compose up -d

# Wait for startup
sleep 5

# Verify restore
docker compose exec magpie magpie-ctl gc --reconcile-only

echo "Restore completed. Verifying..."
echo "Run: docker compose exec magpie magpie-ctl token list"
```

### Partial Restore (Specific Artifacts)

To restore only specific artifacts from backup:

```bash
# Restore single artifact path
rsync -av "$BACKUP_PATH/artifacts/images/ubuntu/" \
  /data/artifacts/images/ubuntu/

# Reconcile symlinks after partial restore
docker compose exec magpie magpie-ctl gc --reconcile-only
```

### Database-Only Restore

To restore only the token database:

```bash
# Stop service to avoid corruption
docker compose stop magpie

# Restore database file
cp "$BACKUP_PATH/magpie.db" /data/artifacts/.magpie.db

# Checkpoint any WAL data to ensure database consistency
sqlite3 /data/artifacts/.magpie.db "PRAGMA wal_checkpoint(TRUNCATE);"

# Set permissions
chown 1000:1000 /data/artifacts/.magpie.db

# Start service
docker compose start magpie

# Verify tokens
docker compose exec magpie magpie-ctl token list
```

---

## Recovery Scenarios

### Scenario 1: Corrupted Manifest Recovery

**Symptoms:**
- `magpie ls` shows incorrect tags
- Symlinks point to wrong blobs
- Metadata missing or corrupt

**Cause:** Manifest file (`.magpie`) corrupted or manually edited incorrectly.

**Recovery:**

If you have a recent backup:

```bash
# Restore manifest from backup
ARTIFACT_PATH="images/ubuntu"
cp "$BACKUP_PATH/artifacts/$ARTIFACT_PATH/.magpie" \
  "/data/artifacts/$ARTIFACT_PATH/.magpie"

# Reconcile symlinks from restored manifest
docker compose exec magpie magpie-ctl gc --reconcile-only
```

If no backup exists, you can rebuild manually:

```bash
# List available blobs
ls -l /data/artifacts/images/ubuntu/blobs/

# Manually recreate manifest (example structure)
# Note: Manifest only contains tags; metadata is stored in separate
# sidecar files at metadata/{hash}.json (uploaded_by, uploaded_at, source_uri)
cat > /data/artifacts/images/ubuntu/.magpie <<'EOF'
{
  "version": 1,
  "tags": {
    "latest": "@abc12345",
    "v2.0": "@abc12345"
  }
}
EOF

# Reconcile symlinks
docker compose exec magpie magpie-ctl gc --reconcile-only
```

**Verification:**
```bash
# Check tags are correct
magpie ls images/ubuntu

# Verify file integrity
magpie get images/ubuntu:latest --no-verify
sha256sum ubuntu.qcow2  # Compare with expected hash
```

### Scenario 2: Lost Database (Token Regeneration)

**Symptoms:**
- `.magpie.db` file missing or corrupted
- All API calls return 401 Unauthorized
- Cannot authenticate with existing tokens

**Cause:** Database file deleted, corrupted, or disk failure.

**Impact:** All tokens are lost. Artifact data (blobs, manifests) unaffected.

**Recovery:**

1. **Initialize new database and generate new admin token:**

```bash
docker compose exec magpie magpie-ctl init --reset-admin-token
```

Output will show new admin token:
```
============================================================
ADMIN TOKEN (store securely, only shown once!):
mgp_new_admin_token_xyz789...
============================================================
```

2. **Recreate service tokens:**

```bash
# Create new CI token
docker compose exec magpie magpie-ctl token create \
  --name ci-bot --scope write

# Create new read-only token
docker compose exec magpie magpie-ctl token create \
  --name viewer --scope read
```

3. **Update token references:**

- Update CI/CD pipelines with new tokens
- Update client configurations (`~/.magpie/config.toml`)
- Update any scripts using hardcoded tokens

**Prevention:**
- Back up `.magpie.db` separately with higher frequency
- Use SQLite online backup during active use
- Consider replicating database to secondary storage

**Alternative - Restore from backup:**

If you have a database backup:

```bash
docker compose stop magpie
cp "$BACKUP_PATH/magpie.db" /data/artifacts/.magpie.db
chown 1000:1000 /data/artifacts/.magpie.db
docker compose start magpie
```

### Scenario 3: Partial Data Loss (Blob Recovery)

**Symptoms:**
- Some artifacts fail to download (404 errors)
- Symlinks broken (point to non-existent blobs)
- Disk errors or file corruption

**Cause:** Disk failure, accidental deletion, or filesystem corruption.

**Recovery:**

1. **Identify missing blobs:**

```bash
# Check for broken symlinks
find /data/artifacts -type l ! -exec test -e {} \; -print

# Run GC with reconcile to identify issues
docker compose exec magpie magpie-ctl gc --reconcile-only
```

2. **If blobs exist in backup:**

```bash
# Restore missing blobs from backup
ARTIFACT="images/ubuntu"
MISSING_HASH="abc12345"

rsync -av "$BACKUP_PATH/artifacts/$ARTIFACT/blobs/$MISSING_HASH" \
  "/data/artifacts/$ARTIFACT/blobs/$MISSING_HASH"
```

3. **If blobs permanently lost:**

You'll need to re-upload the artifact or restore from the original source:

```bash
# Re-upload artifact (requires original file)
magpie push original-file.tar.gz --to images/ubuntu

# Or restore from S3/backup source
```

4. **Clean up references to lost blobs:**

```bash
# Remove broken symlinks
find /data/artifacts -type l ! -exec test -e {} \; -delete

# Update manifests to remove references (manual edit)
vi /data/artifacts/images/ubuntu/.magpie

# Or remove all untagged/unreferenced blobs
docker compose exec magpie magpie-ctl gc --retention-days 0
```

**Verification:**
```bash
# Verify all artifacts are accessible
magpie ls images/ubuntu

# Test downloads
magpie get images/ubuntu:latest
```

### Scenario 4: Complete Server Loss

**Symptoms:**
- Server hardware failure
- Complete storage loss
- Need to rebuild from scratch

**Recovery:**

1. **Provision new server:**

```bash
# Install Docker and Docker Compose
curl -fsSL https://get.docker.com | sh

# Clone repository
git clone https://github.com/SouthwestCCDC/magpie.git
cd magpie
```

2. **Restore configuration:**

```bash
# Copy configuration from backup
cp "$BACKUP_PATH/env" .env
cp "$BACKUP_PATH/docker-compose.prod.yml" .
cp "$BACKUP_PATH/Caddyfile.prod" .
```

3. **Restore storage:**

```bash
# Create storage directory
mkdir -p /data/artifacts

# Restore from backup
rsync -av "$BACKUP_PATH/artifacts/" /data/artifacts/

# Set permissions
chown -R 1000:1000 /data/artifacts
```

4. **Start services:**

```bash
docker compose -f docker-compose.prod.yml up -d
```

5. **Verify integrity:**

```bash
# Reconcile symlinks
docker compose exec magpie magpie-ctl gc --reconcile-only

# Test authentication
docker compose exec magpie magpie-ctl token list

# Test artifact access
magpie ls images/ubuntu
magpie get images/ubuntu:latest
```

### Scenario 5: Split-Brain (Symlink Drift)

**Symptoms:**
- Symlinks don't match manifest
- Tags point to wrong versions
- Occurred after manual filesystem edits

**Cause:** Direct filesystem manipulation bypassing Magpie API.

**Recovery:**

```bash
# Reconcile symlinks from manifests (safe, idempotent)
docker compose exec magpie magpie-ctl gc --reconcile-only
```

This command:
- Reads all `.magpie` manifest files
- Recreates symlinks to match manifest state
- Removes broken/orphaned symlinks
- Does NOT delete any blobs

**Verification:**
```bash
# Check symlinks match manifests
magpie ls images/ubuntu
ls -l /data/artifacts/images/ubuntu/

# Test downloads
magpie get images/ubuntu:latest
```

---

## Automation

### Systemd Timer (Recommended)

Create a systemd service and timer for automated backups:

**Service file** (`/etc/systemd/system/magpie-backup.service`):

```ini
[Unit]
Description=Magpie Backup Service
After=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/backup-magpie.sh
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Timer file** (`/etc/systemd/system/magpie-backup.timer`):

```ini
[Unit]
Description=Magpie Backup Timer
Requires=magpie-backup.service

[Timer]
# Run daily at 2 AM
OnCalendar=*-*-* 02:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

**Enable and start:**

```bash
# Copy backup script
cp backup-magpie.sh /usr/local/bin/
chmod +x /usr/local/bin/backup-magpie.sh

# Enable timer
systemctl daemon-reload
systemctl enable --now magpie-backup.timer

# Check status
systemctl status magpie-backup.timer
systemctl list-timers magpie-backup.timer
```

**View logs:**
```bash
journalctl -u magpie-backup.service -n 50
```

### Cron Alternative

Add to root's crontab:

```bash
# Edit crontab
sudo crontab -e

# Add daily backup at 2 AM
0 2 * * * /usr/local/bin/backup-magpie.sh >> /var/log/magpie-backup.log 2>&1
```

### Backup Retention

Add rotation to backup script:

```bash
# At end of backup-magpie.sh
# Keep last 30 daily backups
find /backup/magpie -mindepth 1 -maxdepth 1 -type d -mtime +30 -exec rm -rf {} \;

# Or keep last 7 backups
ls -t /backup/magpie | tail -n +8 | xargs -I {} rm -rf /backup/magpie/{}
```

---

## Verification

### Backup Verification Checklist

After creating a backup, verify its integrity:

```bash
#!/bin/bash
# verify-backup.sh - Verify backup integrity

BACKUP_PATH="/backup/magpie/20260115-103000"

echo "=== Backup Verification ==="

# Check backup exists
if [ ! -d "$BACKUP_PATH" ]; then
  echo "[FAIL] Backup directory not found"
  exit 1
fi
echo "[OK] Backup directory exists"

# Check storage directory
if [ ! -d "$BACKUP_PATH/artifacts" ]; then
  echo "[FAIL] Storage directory not backed up"
  exit 1
fi
echo "[OK] Storage directory backed up"

# Check database
if [ ! -f "$BACKUP_PATH/magpie.db" ] && [ ! -f "$BACKUP_PATH/artifacts/.magpie.db" ]; then
  echo "[WARN] Database not found in backup"
else
  echo "[OK] Database backed up"
fi

# Count artifacts
ARTIFACT_COUNT=$(find "$BACKUP_PATH/artifacts" -name ".magpie" | wc -l)
echo "[OK] Found $ARTIFACT_COUNT artifact manifests"

# Count blobs
BLOB_COUNT=$(find "$BACKUP_PATH/artifacts" -type f -path "*/blobs/*" | wc -l)
echo "[OK] Found $BLOB_COUNT blobs"

# Check size
SIZE=$(du -sh "$BACKUP_PATH" | cut -f1)
echo "[OK] Backup size: $SIZE"

# Verify manifest JSON syntax
echo "Checking manifest files..."
INVALID_COUNT=0
while IFS= read -r manifest; do
  if ! jq . "$manifest" > /dev/null 2>&1; then
    echo "[FAIL] Invalid JSON in $manifest"
    INVALID_COUNT=$((INVALID_COUNT + 1))
  fi
done < <(find "$BACKUP_PATH/artifacts" -name ".magpie")

if [ $INVALID_COUNT -gt 0 ]; then
  echo "[FAIL] Found $INVALID_COUNT invalid manifest(s)"
  exit 1
fi
echo "[OK] All manifests are valid JSON"

echo ""
echo "=== Verification Complete ==="
```

### Restore Test (Non-Destructive)

Test restore procedure without affecting production:

```bash
#!/bin/bash
# test-restore.sh - Test restore in isolated environment

BACKUP_PATH="/backup/magpie/20260115-103000"
TEST_PATH="/tmp/magpie-restore-test"

# Create test environment
rm -rf "$TEST_PATH"
mkdir -p "$TEST_PATH/artifacts"

# Restore to test location
rsync -av "$BACKUP_PATH/artifacts/" "$TEST_PATH/artifacts/"

# Verify structure
echo "Checking restored structure..."
MANIFESTS=$(find "$TEST_PATH/artifacts" -name ".magpie" | wc -l)
BLOBS=$(find "$TEST_PATH/artifacts" -type f -path "*/blobs/*" | wc -l)

echo "Restored $MANIFESTS manifests and $BLOBS blobs"

# Check manifest integrity
INVALID_MANIFESTS=0
while IFS= read -r manifest; do
  if ! jq . "$manifest" > /dev/null 2>&1; then
    echo "WARN: Invalid manifest: $manifest"
    INVALID_MANIFESTS=$((INVALID_MANIFESTS + 1))
  fi
done < <(find "$TEST_PATH/artifacts" -name ".magpie")
if [ $INVALID_MANIFESTS -gt 0 ]; then
  echo "ERROR: Found $INVALID_MANIFESTS invalid manifests"
  exit 1
fi

# Cleanup
rm -rf "$TEST_PATH"

echo "Restore test completed successfully"
```

### Monitoring Backup Health

Monitor backup success with these checks:

```bash
# Check last backup age
LAST_BACKUP=$(ls -td /backup/magpie/* | head -1)
BACKUP_AGE=$(( ($(date +%s) - $(stat -c %Y "$LAST_BACKUP")) / 3600 ))
echo "Last backup: $BACKUP_AGE hours ago"

# Alert if backup older than 36 hours
if [ $BACKUP_AGE -gt 36 ]; then
  echo "WARNING: Backup is stale!"
fi

# Check backup size trend
du -sh /backup/magpie/* | tail -5
```

---

## Best Practices

1. **3-2-1 Rule:** 3 copies of data, 2 different media types, 1 copy offsite
2. **Backup Frequency:** Daily for general storage, more frequent for critical artifacts
3. **Testing:** Monthly restore tests, quarterly full restore drills
4. **Encrypt backups:** Use GPG or equivalent for backups at rest
5. **Secure storage:** Restrict access, separate credentials, audit logs
6. **Token security:** Never commit `.magpie.db` to version control, rotate after restore if compromised

---

## Troubleshooting

### Backup Issues

**Backup script fails with "Permission denied":**
```bash
# Fix permissions
sudo chown -R root:root /data/artifacts
sudo chmod -R 755 /data/artifacts

# Run backup as root or with Docker exec
docker compose exec magpie tar czf /tmp/backup.tar.gz /data/artifacts
```

**Backup taking too long:**
```bash
# Use compression
rsync -avz --exclude='.tmp/' /data/artifacts/ /backup/magpie/

# Exclude large untagged blobs
rsync -av --exclude='.tmp/' --exclude='*/blobs/*' /data/artifacts/ /backup/manifests-only/

# Split backup: critical data first, bulk data second
```

**Out of disk space:**
```bash
# Check backup storage
df -h /backup

# Clean old backups
find /backup/magpie -mindepth 1 -maxdepth 1 -type d -mtime +30 -exec rm -rf {} \;

# Compress old backups
find /backup/magpie -mindepth 1 -maxdepth 1 -type d -mtime +7 | xargs -I {} tar czf {}.tar.gz {} && rm -rf {}
```

### Restore Issues

**Restored artifacts fail hash verification:**

This indicates backup corruption or incomplete transfer.

```bash
# Re-run restore with checksums
rsync -avc --checksum "$BACKUP_PATH/artifacts/" /data/artifacts/

# Verify blob integrity
find /data/artifacts -type f -path "*/blobs/*" | while read blob; do
  hash=$(basename "$blob")
  actual=$(sha256sum "$blob" | cut -d' ' -f1)
  if [ "${actual:0:8}" != "$hash" ]; then
    echo "CORRUPT: $blob"
  fi
done
```

**Database restore fails - "database is locked":**
```bash
# Stop all services accessing database
docker compose stop magpie

# Check for WAL files
ls -l /data/artifacts/.magpie.db*

# Restore with WAL checkpoint
sqlite3 /data/artifacts/.magpie.db "PRAGMA wal_checkpoint(TRUNCATE);"

# Now restore
cp "$BACKUP_PATH/magpie.db" /data/artifacts/.magpie.db
```

**Service won't start after restore:**
```bash
# Check logs
docker compose logs magpie

# Verify permissions
ls -l /data/artifacts

# Fix permissions
sudo chown -R 1000:1000 /data/artifacts
chmod 755 /data/artifacts

# Verify database isn't corrupted
sqlite3 /data/artifacts/.magpie.db "PRAGMA integrity_check;"
```

---

## Additional Resources

- [Magpie User Guide](user-guide.md) - General usage and CLI commands
- [Magpie Design Document](design.md) - Architecture and implementation details
- [SQLite Backup Documentation](https://www.sqlite.org/backup.html) - SQLite online backup API
- [rsync Manual](https://linux.die.net/man/1/rsync) - rsync options and examples

---

## Quick Reference

| Task | Command |
|------|---------|
| Full backup | `rsync -av --exclude='.tmp/' /data/artifacts/ /backup/magpie/` |
| Database backup | `docker compose exec magpie sqlite3 /data/artifacts/.magpie.db ".backup '/tmp/backup.db'"` |
| Full restore | `rsync -av /backup/magpie/artifacts/ /data/artifacts/` |
| Reconcile symlinks | `docker compose exec magpie magpie-ctl gc --reconcile-only` |

---

*This documentation was generated with AI assistance (Claude Code w/ Opus 4.5)*
