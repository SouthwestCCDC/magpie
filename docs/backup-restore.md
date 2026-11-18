# Backup and Restore

## Storage Layout

```
/data/
├── magpie.db               # Token database (back up using SQLite's backup command)
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

Upgrading without the installer -- changing the image tag and restarting
the container -- migrates the database automatically at startup (see
[Upgrading to v0.2.0](installation.md#upgrading-to-v020)). It only keeps
the [pre-migration copy of the database](#pre-migration-database-copies),
on the same volume, so take a [manual backup](#backup-procedures) first. A container
whose database is newer than its image supports refuses to start and
leaves the database untouched; run a newer image, revert the data with
the newer image's `magpie-ctl migrate --to N` (see [Downgrading the data
format](installation.md#downgrading-the-data-format)), or
[restore](#restore-procedures) a backup.

**Layout:** `<install-dir>/backups/<version>-<UTC-timestamp>/`, e.g.
`/opt/magpie/backups/0.2.0-20260720T073811Z/`:

```
<install-dir>/backups/<version>-<timestamp>/
├── MANIFEST     # source/target version, image, git ref, DB sha256, backup mode
├── rollback/    # config snapshot: .env, docker-compose.yml, systemd units,
│                #   prior image tag, prior git ref
└── data/        # data snapshot: magpie.db(+wal/shm), .env, admin-token,
                 #   and, best-effort per --backup-artifacts, the artifacts tree
```

The artifacts tree is **not guaranteed** to be present even with the
default `--backup-artifacts=link`: hardlinking falls back to skipping
(not copying) if it fails, e.g. when `MAGPIE_DATA_DIR` is on a different
filesystem than the backup location. Each backup's own `MANIFEST` records
`backup_artifacts_mode` (`link`, `copy`, or `skip`) -- that field, not
this table, is the authoritative record of what a given backup actually
contains. The bind-mounted artifacts themselves are always untouched by
an update regardless, so this only affects the backup's own
defense-in-depth copy, not data safety.

`<install-dir>/backups/` is under `INSTALL_DIR`, deliberately **not**
under `MAGPIE_DATA_DIR` -- so a data-dir wipe can't take the backup with
it.

**Flags** (all optional; see `magpie-deploy.sh update --help`):

| Flag | Default | Effect |
|---|---|---|
| `--no-backup` | off | Skip the data backup entirely. NOT recommended -- if the update then fails its post-update checks, rollback can only restore config/units/image, not data. |
| `--keep-backups N` | `3` | How many past backups to retain. Pruned only after a *successful* update; every backup from a failed update is kept for forensics/manual recovery. |
| `--backup-artifacts MODE` | `link` | How to back up the artifacts tree: `link` (hardlink snapshot, near-free, same filesystem only -- falls back to *skipping* the artifacts backup, not copying, if hardlinking fails, e.g. cross-filesystem), `copy` (full independent copy), or `skip`. The bind-mounted artifacts themselves are untouched by an update regardless -- this only affects the backup's own defense-in-depth copy. Check a given backup's `MANIFEST` (`backup_artifacts_mode`) for what it actually contains. |
| `--no-rollback` | off | Don't automatically roll back on a failed post-update assertion -- leaves the failed new stack running so you can investigate in place. |

On a failed update, the installer prints the backup path and (for a
failed rollback restore itself) the `MANIFEST` location -- start there
for manual recovery.

## Pre-Migration Database Copies

Whenever `magpie-ctl migrate` is about to change the database -- at
container start when the data format is behind the image, or with
`migrate --to N` -- it first saves a copy of `magpie.db` with SQLite's
online backup:

```
<data>/backups/magpie.db.<UTC timestamp>.pre-v<N>            # before migrating to N
<data>/backups/magpie.db.<UTC timestamp>.pre-revert-to-v<N>  # before migrate --to N
```

The directory is `0700` and each copy `0600` (it holds token hashes). The
newest 3 are kept, pruned only after a migration succeeds; a failed
attempt removes its own copy, so retrying can't evict older ones. Other
files you put in the directory are never pruned. If the copy can't be
written (e.g. the volume is full), nothing is migrated and the container
doesn't start; delete old copies or free space. A fresh install takes one copy too, since `init` creates a
version-0 database that is then migrated.

These protect against a bad migration, not against losing the volume, and
don't include artifacts (migrations don't change them). To go back to one,
stop magpie and put it in place. `cp -p` keeps the copy's owner, which is
the uid magpie runs as (run it as root, or as that uid):

```bash
cp -p <data>/backups/magpie.db.<timestamp>.pre-v<N> <data>/magpie.db
rm -f <data>/magpie.db-wal <data>/magpie.db-shm
```

Then start the image you want: the older one the copy was taken from, or
a fixed newer one, which migrates it forward again. Tokens created after
the copy was taken are lost.

## Backup Procedures

Manual backup, independent of `magpie-deploy.sh update`'s automatic
pre-update backup above -- for a standalone backup schedule, or a
non-installer (`docker compose`-direct) deployment.

For the Compose commands below, work in the directory with the release Compose file
and its `.env`. On an installer-managed host, use `<install dir>/bin/magpie-compose`
instead of `docker compose` for container commands, and manage the stack with systemd.
Set `MAGPIE_DATA_DIR` in your shell to the actual host data directory; the shell
commands do not read the Compose env file.

**Full backup (rsync):**
Pause uploads, tag changes, and scheduled GC while copying the artifact tree for a
consistent snapshot. The example sets the backup root to mode `0700` before copying:
`rsync -a` can preserve wider permissions on the snapshot itself, which can contain
the bootstrap admin token. The examples use the default database and staging paths.
```bash
(
  set -e
  umask 077
  MAGPIE_DATA_DIR="${MAGPIE_DATA_DIR:-./data}"
  BACKUP_ROOT="/backup/magpie"
  mkdir -p -- "$BACKUP_ROOT"
  chmod 0700 -- "$BACKUP_ROOT"
  BACKUP_PATH="$BACKUP_ROOT/$(date +%Y%m%d-%H%M%S)"
  mkdir -- "$BACKUP_PATH"
  rsync -av --exclude='artifacts/.tmp/' --exclude='magpie.db*' "$MAGPIE_DATA_DIR/" "$BACKUP_PATH/"
  sqlite3 "$MAGPIE_DATA_DIR/magpie.db" ".backup '$BACKUP_PATH/magpie.db'"
)
```

**S3 backup** (tagged artifacts only):
```bash
docker compose exec magpie magpie-ctl sync to-s3 --dry-run
docker compose exec magpie magpie-ctl sync to-s3
```
Requires `MAGPIE_S3_BUCKET` environment variable. Note: Backs up artifacts only; separately backup `magpie.db`.

**WARNING:** S3 restore using `rclone sync` is destructive and will delete local files not present in S3 (including untagged blobs). Back up `magpie.db` separately, as it is not synced to S3. Always use `--dry-run` first to preview changes before running restore operations.

## Restore Procedures

**Full restore:** Stop scheduled GC and the stack before replacing the data directory.
On an installer-managed host, stop and restart via systemd, not Compose.

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
docker compose up -d --wait
docker compose exec magpie magpie-ctl gc --reconcile-only
```

**Tokens-only restore:** Stop all processes using the database first. For an
installer-managed stack, stop `magpie-gc.timer`, `magpie-gc.service`, and
`magpie.service` before replacing the file, then restart the stack and timer.
Keep a separate copy of the existing database and its sidecar files before replacing
them. The backup made with SQLite's `.backup` is standalone; do not reuse WAL or
shared-memory files from the database you're replacing.

```bash
docker compose stop magpie
rm -f "$MAGPIE_DATA_DIR/magpie.db-wal" "$MAGPIE_DATA_DIR/magpie.db-shm"
cp "$BACKUP_PATH/magpie.db" "$MAGPIE_DATA_DIR/magpie.db"
chown "$(stat -c %u:%g "$MAGPIE_DATA_DIR")" "$MAGPIE_DATA_DIR/magpie.db"
chmod 0600 "$MAGPIE_DATA_DIR/magpie.db"
docker compose start magpie
```

## Recovery

**Lost token database:**
The bundled container initializes a missing database on startup. Restart it and
retrieve the new token via the configured sink, then reissue scoped tokens. To replace
the bootstrap token in a running stack instead, use `magpie-ctl init --reset-admin-token`.

```bash
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

*(AI-generated via Claude Code w/ Sonnet 4.5; automatic pre-update backup section added for issue #561 via Claude Code w/ Opus 4.8; v0.2.1 docs audit via Devin)*
