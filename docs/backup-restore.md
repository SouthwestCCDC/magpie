# Magpie Backup and Restore Guide

This guide covers backup and restore procedures for Magpie artifact storage.

---

## What to Back Up

Magpie stores data in these locations:

| Component | Default Location | Description |
|-----------|-----------------|-------------|
| **Blob files** | `{storage}/**/blobs/` | Actual artifact content |
| **Manifests** | `{storage}/**/.magpie` | Tag-to-hash mappings (JSON) |
| **Metadata** | `{storage}/**/metadata/` | Upload provenance (uploaded_by, uploaded_at, source_uri) |
| **SQLite database** | `/data/magpie.db` | Authentication tokens |

Default storage path: `/data/artifacts` (configurable via `MAGPIE_STORAGE_PATH`).
Default database path: `/data/magpie.db` (configurable via `MAGPIE_DATABASE_PATH`).

### Storage Directory Structure

```
/data/
├── magpie.db               # SQLite database (tokens only)
├── artifacts/              # Artifact storage root
│   ├── .tmp/               # Temporary upload staging (exclude from backups)
│   └── {artifact-path}/    # Artifact directories (e.g., images/ubuntu/)
│       ├── .magpie         # Manifest JSON
│       ├── blobs/          # Content-addressed storage
│       │   └── {hash}      # Blob files (8-char SHA-256 prefix)
│       ├── metadata/       # Provenance sidecars
│       │   └── {hash}.json # uploaded_by, uploaded_at, source_uri
│       └── latest -> blobs/{hash}  # Tag symlinks
```

### Manifest Format

The manifest file (`.magpie`) stores tag-to-hash mappings using full SHA-256 hashes (64 hex characters). Blobs and symlinks use only the first 8 characters of the hash for filenames, but the manifest stores the complete hash for verification.

```json
{
  "version": 1,
  "tags": {
    "latest": "abc123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "v2.0": "def67890ab123456789abcdef0123456789abcdef0123456789abcdef012345678"
  }
}
```

### Metadata Sidecar Format

Metadata files (`metadata/{hash}.json`) store the full SHA-256 hash and upload provenance:

```json
{
  "hash": "abc123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "uploaded_by": "ci-bot",
  "uploaded_at": "2026-01-15T10:30:00Z",
  "source_uri": null
}
```

---

## Backup Procedures

### Full Backup with rsync

```bash
# Use the same data directory as your docker-compose deployment (HOST path, not container path)
# This should match the host path you mounted (e.g., ./data or /opt/magpie/data)
# Inside containers, this is always /data, but on the host it depends on your docker-compose.yml
# Default in docker-compose.yml is ./data (relative), not /data (absolute)
MAGPIE_DATA_DIR="${MAGPIE_DATA_DIR:-./data}"
BACKUP_PATH="/backup/magpie/$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_PATH"

# Backup storage (exclude temp directory)
rsync -av --exclude='artifacts/.tmp/' "$MAGPIE_DATA_DIR/" "$BACKUP_PATH/"

# Alternatively, backup database separately using SQLite online backup (safe during writes)
# Note: sqlite3 must be installed on the host (not in container)
# sqlite3 "$MAGPIE_DATA_DIR/magpie.db" ".backup '$BACKUP_PATH/magpie.db'"
```

### S3 Backup with magpie-ctl sync

The built-in sync commands back up tagged artifacts to S3.

**For container deployments**, run these commands inside the container:

```bash
# Sync tagged artifacts to S3
docker compose exec magpie magpie-ctl sync to-s3

# Preview what would be synced
docker compose exec magpie magpie-ctl sync to-s3 --dry-run

# Preview restore from S3 (safe, no --force needed)
docker compose exec magpie magpie-ctl sync from-s3 --dry-run

# Restore from S3 (requires --force if existing Magpie manifests are present; will overwrite)
docker compose exec magpie magpie-ctl sync from-s3 --force
```

**For host installations**, run `magpie-ctl` directly (ensure it points to the same data directory).

Requires `MAGPIE_S3_BUCKET` environment variable. Optionally set `MAGPIE_S3_PREFIX` for key prefixes.

**Note:** When using `docker compose exec`, the S3 environment variables (`MAGPIE_S3_BUCKET` and `MAGPIE_S3_PREFIX`) are not set in the container by default. You must either:
- Add them to the `environment` section in `docker-compose.yml`, or
- Pass them inline with `-e` flags: `docker compose exec -e MAGPIE_S3_BUCKET=my-bucket -e MAGPIE_S3_PREFIX=backup magpie magpie-ctl sync to-s3`

**Important:** The sync commands only back up artifact data (manifests, blobs, metadata sidecars). They do NOT back up the token database (`magpie.db`) or configuration files. Back up the database using the rsync/SQLite procedure above. Configuration files (`.env`, `docker-compose.yml`, `Caddyfile`) require separate manual backup (e.g., `cp .env docker-compose.yml Caddyfile "$BACKUP_PATH/"`).

The sync commands require either `rclone` or `aws` CLI. If `rclone` is available, it uses `--checksum` for content-based comparison. If falling back to AWS CLI, uploads use `aws s3 cp` (overwrites on each run), while restores use `aws s3 sync` (incremental).

**Warning - Destructive Behavior:** When using `rclone` for `from-s3` restores, `rclone sync` will DELETE any local files that don't exist in S3. This includes:
- **Untagged blobs** not backed up to S3 (only tagged artifacts are synced)
- **The token database** (`magpie.db`) since `to-s3` does not upload it

**Critical:** If you restore from S3 to a directory containing `magpie.db`, rclone will delete the database file, requiring token regeneration. To avoid data loss:
- Restore to an empty directory, OR
- Back up `magpie.db` separately before restoring (see Database-Only Restore section), OR
- Use `--dry-run` first to preview what will be deleted

---

## Restore Procedures

### Full Restore

```bash
BACKUP_PATH="/backup/magpie/20260115-103000"
MAGPIE_DATA_DIR="${MAGPIE_DATA_DIR:-./data}"

# Stop services
docker compose down

# Restore data directory to empty directory to avoid stale data
# If directory exists, use rsync --delete or manually clear it first
mkdir -p "$MAGPIE_DATA_DIR"
rsync -av --delete "$BACKUP_PATH/" "$MAGPIE_DATA_DIR/"

# Ensure temp directory exists before setting ownership
mkdir -p "$MAGPIE_DATA_DIR/artifacts/.tmp"

# Set ownership to match MAGPIE_UID/MAGPIE_GID or the data directory owner
# The entrypoint will auto-detect from directory ownership
OWNER_UID=$(stat -c %u "$MAGPIE_DATA_DIR")
OWNER_GID=$(stat -c %g "$MAGPIE_DATA_DIR")
chown -R "$OWNER_UID:$OWNER_GID" "$MAGPIE_DATA_DIR"

# Start services and reconcile symlinks
docker compose up -d
sleep 5
docker compose exec magpie magpie-ctl gc --reconcile-only
```

### Database-Only Restore

For restoring tokens without touching artifacts:

```bash
BACKUP_PATH="/backup/magpie/20260115-103000"
MAGPIE_DATA_DIR="${MAGPIE_DATA_DIR:-./data}"

docker compose stop magpie
cp "$BACKUP_PATH/magpie.db" "$MAGPIE_DATA_DIR/magpie.db"
sqlite3 "$MAGPIE_DATA_DIR/magpie.db" "PRAGMA wal_checkpoint(TRUNCATE);"

# Set ownership to match data directory owner
OWNER_UID=$(stat -c %u "$MAGPIE_DATA_DIR")
OWNER_GID=$(stat -c %g "$MAGPIE_DATA_DIR")
chown "$OWNER_UID:$OWNER_GID" "$MAGPIE_DATA_DIR/magpie.db"

docker compose start magpie
docker compose exec magpie magpie-ctl token list
```

---

## Recovery Scenarios

### Lost Database (Token Regeneration)

If `magpie.db` is lost or corrupted, artifact data is unaffected. Generate new tokens:

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
find "${MAGPIE_DATA_DIR:-./data}/artifacts" -type l ! -exec test -e {} \; -print
```

Restore missing blobs from backup, or re-upload from original source.

---

## Quick Reference

Note: Set `BACKUP_PATH` (e.g., `BACKUP_PATH="/backup/magpie/$(date +%Y%m%d-%H%M%S)"`) before using backup/restore commands.

| Task | Command |
|------|---------|
| Full backup | `rsync -av --exclude='artifacts/.tmp/' ${MAGPIE_DATA_DIR:-./data}/ $BACKUP_PATH/` |
| Database backup | `sqlite3 ${MAGPIE_DATA_DIR:-./data}/magpie.db ".backup '$BACKUP_PATH/magpie.db'"` |
| Full restore | `rsync -av --delete $BACKUP_PATH/ ${MAGPIE_DATA_DIR:-./data}/` |
| Reconcile symlinks | `docker compose exec magpie magpie-ctl gc --reconcile-only` |
| Reset admin token | `docker compose exec magpie magpie-ctl init --reset-admin-token` |
| GC untagged blobs older than 90 days | `docker compose exec magpie magpie-ctl gc --retention-days 90` |

---

*This documentation was generated with AI assistance (Claude Code w/ Opus 4.5)*
