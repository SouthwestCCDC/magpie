# Magpie Backup and Restore Guide

This guide covers backup and restore procedures for Magpie artifact storage.

---

## What to Back Up

Magpie stores data in these locations:

| Component | Default Location | Description |
|-----------|-----------------|-------------|
| **Blob files** | `{storage}/*/blobs/` | Actual artifact content |
| **Manifests** | `{storage}/*/.magpie` | Tag-to-hash mappings (JSON) |
| **Metadata** | `{storage}/*/metadata/` | Upload provenance (uploader, timestamps) |
| **SQLite database** | `{storage}/.magpie.db` | Authentication tokens |

Default storage path: `/data/artifacts` (configurable via `MAGPIE_STORAGE_PATH`).

### Storage Directory Structure

```
/data/artifacts/
├── .magpie.db              # SQLite database (tokens only)
├── .tmp/                   # Temporary upload staging (exclude from backups)
├── {artifact-path}/        # Artifact directories (e.g., images/ubuntu/)
│   ├── .magpie             # Manifest JSON
│   ├── blobs/              # Content-addressed storage
│   │   └── {hash}          # Blob files (8-char SHA-256 prefix)
│   ├── metadata/           # Provenance sidecars
│   │   └── {hash}.json     # uploader, timestamp, source_uri
│   └── latest -> blobs/{hash}  # Tag symlinks
```

### Manifest Format

```json
{
  "version": 1,
  "tags": {
    "latest": "@abc12345",
    "v2.0": "@abc12345"
  }
}
```

### Metadata Sidecar Format

```json
{
  "hash": "abc12345...",
  "uploaded_by": "ci-bot",
  "uploaded_at": "2026-01-15T10:30:00Z",
  "source_uri": null
}
```

---

## Backup Procedures

### Full Backup with rsync

```bash
STORAGE_PATH="/data/artifacts"
BACKUP_PATH="/backup/magpie/$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_PATH"

# Backup storage (exclude temp directory)
rsync -av --exclude='.tmp/' "$STORAGE_PATH/" "$BACKUP_PATH/artifacts/"

# SQLite online backup (safe during writes)
# Note: sqlite3 must be installed on the host (not in container)
# Run against the mounted volume path (adjust if your compose mount differs)
MAGPIE_DATA_DIR="${MAGPIE_DATA_DIR:-/data/artifacts}"
sqlite3 "$MAGPIE_DATA_DIR/.magpie.db" ".backup '$BACKUP_PATH/magpie.db'"
```

### S3 Backup with magpie-ctl sync

The built-in sync commands back up tagged artifacts to S3:

```bash
# Sync tagged artifacts to S3
magpie-ctl sync to-s3

# Preview what would be synced
magpie-ctl sync to-s3 --dry-run

# Preview restore from S3 (safe, no --force needed)
magpie-ctl sync from-s3 --dry-run

# Restore from S3 (requires --force if local artifacts exist; will overwrite)
magpie-ctl sync from-s3 --force
```

Requires `MAGPIE_S3_BUCKET` environment variable. Optionally set `MAGPIE_S3_PREFIX` for key prefixes.

**Important:** The sync commands only back up artifact data (manifests, blobs, metadata sidecars). They do NOT back up the token database (`.magpie.db`) or configuration files. Back these up separately using the rsync procedure above.

The sync commands require either `rclone` or `aws` CLI. If `rclone` is available, it uses `--checksum` for content-based comparison. If falling back to AWS CLI, uploads use `aws s3 cp` (overwrites on each run), while restores use `aws s3 sync` (incremental).

**Warning - Destructive Behavior:** When using `rclone` for `from-s3` restores, `rclone sync` will DELETE any local blobs that don't exist in S3. If you have untagged local blobs not backed up to S3, they will be removed. To avoid data loss, restore to an empty directory or use `--dry-run` first to preview what will be deleted.

---

## Restore Procedures

### Full Restore

```bash
BACKUP_PATH="/backup/magpie/20260115-103000"
STORAGE_PATH="/data/artifacts"

# Stop services
docker compose down

# Restore storage
mkdir -p "$STORAGE_PATH"
rsync -av "$BACKUP_PATH/artifacts/" "$STORAGE_PATH/"

# Restore database
cp "$BACKUP_PATH/magpie.db" "$STORAGE_PATH/.magpie.db"
chown 1000:1000 "$STORAGE_PATH/.magpie.db"

# Ensure temp directory exists
mkdir -p "$STORAGE_PATH/.tmp"
chown -R 1000:1000 "$STORAGE_PATH"

# Start services and reconcile symlinks
docker compose up -d
sleep 5
docker compose exec magpie magpie-ctl gc --reconcile-only
```

### Database-Only Restore

For restoring tokens without touching artifacts:

```bash
MAGPIE_DATA_DIR="${MAGPIE_DATA_DIR:-/data/artifacts}"

docker compose stop magpie
cp "$BACKUP_PATH/magpie.db" "$MAGPIE_DATA_DIR/.magpie.db"
sqlite3 "$MAGPIE_DATA_DIR/.magpie.db" "PRAGMA wal_checkpoint(TRUNCATE);"
chown 1000:1000 "$MAGPIE_DATA_DIR/.magpie.db"
docker compose start magpie
docker compose exec magpie magpie-ctl token list
```

---

## Recovery Scenarios

### Lost Database (Token Regeneration)

If `.magpie.db` is lost or corrupted, artifact data is unaffected. Generate new tokens:

```bash
# Initialize new database and generate admin token
docker compose exec magpie magpie-ctl init --reset-admin-token

# Create service tokens
docker compose exec magpie magpie-ctl token create --name ci-bot --scope write
docker compose exec magpie magpie-ctl token create --name viewer --scope read
```

Update all clients and CI/CD pipelines with new tokens.

### Symlink Drift

If symlinks don't match manifests (e.g., after manual filesystem edits):

```bash
docker compose exec magpie magpie-ctl gc --reconcile-only
```

This reads all `.magpie` manifests and recreates symlinks to match. Safe and idempotent.

### Missing Blobs

To identify broken symlinks (pointing to missing blobs):

```bash
find /data/artifacts -type l ! -exec test -e {} \; -print
```

Restore missing blobs from backup, or re-upload from original source.

---

## Quick Reference

| Task | Command |
|------|---------|
| Full backup | `rsync -av --exclude='.tmp/' /data/artifacts/ /backup/magpie/` |
| Database backup | `sqlite3 /data/artifacts/.magpie.db ".backup './magpie-backup.db'"` |
| Full restore | `rsync -av /backup/magpie/artifacts/ /data/artifacts/` |
| Reconcile symlinks | `magpie-ctl gc --reconcile-only` |
| Reset admin token | `magpie-ctl init --reset-admin-token` |
| GC untagged blobs | `magpie-ctl gc --retention-days 90` |

---

*This documentation was generated with AI assistance (Claude Code w/ Opus 4.5)*
