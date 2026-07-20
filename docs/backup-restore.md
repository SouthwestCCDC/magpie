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

## Automatic Pre-Update Backups

`magpie-deploy.sh update` (the [installer](../scripts/magpie-deploy.sh))
backs up the data directory automatically before every update -- no
manual backup step is required for a routine `update`. See [Upgrading to
v0.2.0](installation.md#upgrading-to-v020) for the full backup -> swap ->
assert -> rollback flow ([issue
#561](https://github.com/SouthwestCCDC/magpie/issues/561)).

**Layout:** `<install-dir>/backups/<version>-<UTC-timestamp>/`, e.g.
`/opt/magpie/backups/0.2.0-20260720T073811Z/`:

```
<install-dir>/backups/<version>-<timestamp>/
├── MANIFEST     # source/target version, image, git ref, DB sha256, backup mode
├── rollback/    # config snapshot: .env, docker-compose.yml, systemd units,
│                #   prior image tag, prior git ref
└── data/        # data snapshot: magpie.db(+wal/shm), .env, admin-token,
                 #   and (per --backup-artifacts) the artifacts tree
```

`<install-dir>/backups/` is under `INSTALL_DIR`, deliberately **not**
under `MAGPIE_DATA_DIR` -- so a data-dir wipe can't take the backup with
it.

**Flags** (all optional; see `magpie-deploy.sh update --help`):

| Flag | Default | Effect |
|---|---|---|
| `--no-backup` | off | Skip the data backup entirely. NOT recommended -- if the update then fails its post-update checks, rollback can only restore config/units/image, not data. |
| `--keep-backups N` | `3` | How many past backups to retain. Pruned only after a *successful* update; every backup from a failed update is kept for forensics/manual recovery. |
| `--backup-artifacts MODE` | `link` | How to back up the artifacts tree: `link` (hardlink snapshot, near-free, same filesystem only), `copy` (full independent copy), or `skip`. The bind-mounted artifacts themselves are untouched by an update regardless -- this only affects the backup's own defense-in-depth copy. |
| `--no-rollback` | off | Don't automatically roll back on a failed post-update assertion -- leaves the failed new stack running so you can investigate in place. |

On a failed update, the installer prints the backup path and (for a
failed rollback restore itself) the `MANIFEST` location -- start there
for manual recovery.

## Backup Procedures

Manual backup, independent of `magpie-deploy.sh update`'s automatic
pre-update backup above -- for a standalone backup schedule, or a
non-installer (`docker compose`-direct) deployment.

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

**WARNING:** S3 restore using `rclone sync` is destructive and will delete local files not present in S3 (including untagged blobs). Back up `magpie.db` separately, as it is not synced to S3. Always use `--dry-run` first to preview changes before running restore operations.

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

---

*(AI-generated via Claude Code w/ Sonnet 4.5; automatic pre-update backup section added for issue #561 via Claude Code w/ Opus 4.8)*
