# Backup and Restore

## Storage Layout

```
/data/
├── magpie.db               # Token database (backup separately)
└── artifacts/
    ├── {artifact-path}/
    │   ├── .magpie         # Tag-to-hash mappings
    │   ├── blobs/{hash}    # Artifact files
    │   ├── metadata/       # Upload provenance
    │   └── {tag} -> blobs/{hash}  # Tag symlinks
    └── .tmp/               # Temporary uploads (exclude from backup)
```

## Backup Procedures

**Full backup (rsync):**
```bash
MAGPIE_DATA_DIR="${MAGPIE_DATA_DIR:-./data}"
BACKUP_PATH="/backup/magpie/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_PATH"
rsync -av --exclude='artifacts/.tmp/' --exclude='magpie.db*' "$MAGPIE_DATA_DIR/" "$BACKUP_PATH/"
sqlite3 "$MAGPIE_DATA_DIR/magpie.db" ".backup '$BACKUP_PATH/magpie.db'"
```

**S3 backup** (tagged artifacts only):
```bash
docker compose exec magpie magpie-ctl sync to-s3 --dry-run
docker compose exec magpie magpie-ctl sync to-s3
```
Requires `MAGPIE_S3_BUCKET` environment variable. Note: Backs up artifacts only; separately backup `magpie.db`.

## Restore Procedures

**Full restore:**
```bash
BACKUP_PATH="/backup/magpie/20260115-103000"
MAGPIE_DATA_DIR="${MAGPIE_DATA_DIR:-./data}"
docker compose down
mkdir -p "$MAGPIE_DATA_DIR"
rsync -av --delete "$BACKUP_PATH/" "$MAGPIE_DATA_DIR/"
mkdir -p "$MAGPIE_DATA_DIR/artifacts/.tmp"
OWNER_UID=$(stat -c %u "$MAGPIE_DATA_DIR")
OWNER_GID=$(stat -c %g "$MAGPIE_DATA_DIR")
chown -R "$OWNER_UID:$OWNER_GID" "$MAGPIE_DATA_DIR"
docker compose up -d && sleep 5
docker compose exec magpie magpie-ctl gc --reconcile-only
```

**Tokens-only restore:**
```bash
docker compose stop magpie
cp "$BACKUP_PATH/magpie.db" "$MAGPIE_DATA_DIR/magpie.db"
sqlite3 "$MAGPIE_DATA_DIR/magpie.db" "PRAGMA wal_checkpoint(TRUNCATE);"
chown "$(stat -c %u:%g "$MAGPIE_DATA_DIR")" "$MAGPIE_DATA_DIR/magpie.db"
docker compose start magpie
```

## Recovery

**Lost token database:**
```bash
docker compose exec magpie magpie-ctl init --reset-admin-token
docker compose exec magpie magpie-ctl token create --name ci-bot --scope write
```

**Symlink corruption:**
```bash
docker compose exec magpie magpie-ctl gc --reconcile-only
```

**Find broken symlinks:**
```bash
find "$MAGPIE_DATA_DIR/artifacts" -type l ! -exec test -e {} \; -print
```
