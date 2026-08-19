# Deployment Configuration

This directory contains deployment configurations for Magpie services.

## Garbage Collection Scheduling

Magpie's garbage collection (GC) removes untagged blobs older than the retention
period (default: 90 days). GC should run periodically to reclaim disk space.

### Quick Start (systemd)

```bash
# Copy unit files
sudo cp systemd/magpie-gc.service /etc/systemd/system/
sudo cp systemd/magpie-gc.timer /etc/systemd/system/

# Reload and enable
sudo systemctl daemon-reload
sudo systemctl enable --now magpie-gc.timer

# Verify
systemctl list-timers magpie-gc.timer
```

### Quick Start (cron)

```bash
# Copy cron configuration
sudo cp cron/magpie-gc /etc/cron.d/
sudo chmod 644 /etc/cron.d/magpie-gc

# Verify (should show in cron logs)
grep magpie-gc /var/log/syslog
```

## GC Scheduling Options

### systemd Timer (Recommended)

The systemd timer provides:
- Persistent scheduling (runs missed jobs after reboot)
- Randomized delay to reduce load spikes
- Integration with journald for logging
- Condition-based execution (skips if lock file exists)

Files:
- `systemd/magpie-gc.service` - Oneshot service that runs GC
- `systemd/magpie-gc.timer` - Timer that triggers the service

Default schedule: Daily at 2:00 AM with up to 30 minutes random delay.

### cron (Alternative)

For systems without systemd or where cron is preferred.

File: `cron/magpie-gc`

Default schedule: Daily at 2:00 AM.

Uses `flock` to prevent concurrent runs.

## Configuration

### Retention Period

The retention period determines how long untagged blobs are kept before becoming
eligible for garbage collection. Tagged blobs are never deleted by GC.

Configure via environment variable in your `.env` or `docker-compose.yml`:

```bash
# In .env file (read by Docker Compose)
MAGPIE_RETENTION_DAYS=30
```

Or via command-line flag (recommended for one-off overrides):

```bash
# For Docker deployments, use CLI flags rather than shell environment variables
# (shell env vars are not passed into the container)
docker compose run --rm magpie magpie-ctl gc --retention-days 30

# Direct execution
magpie-ctl gc --retention-days 30
```

Recommended settings:
- **High-churn environments** (CI/CD): 7-14 days
- **Standard deployments**: 30-90 days (default: 90)
- **Archival storage**: 180+ days

Align your GC schedule with your retention period:
- Shorter retention = more frequent GC (daily or multiple times per day)
- Longer retention = less frequent GC (weekly)

### Lock File

The lock file prevents concurrent GC runs, which could cause race conditions or
excessive resource usage.

Default path: `/var/run/magpie-gc.lock`

**Note:** The `/var/run` directory (typically a symlink to `/run`) must exist
and be writable by the user running GC. This is standard on most Linux systems.

The systemd service uses `flock -n` for atomic locking. The cron configuration
also uses `flock -n`. Unlike `ConditionPathExists`, `flock` automatically
handles stale lock files from crashed processes.

If GC is already running:
- systemd: `flock` exits immediately with status 1 (check `systemctl status magpie-gc`)
- cron: `flock` exits silently with status 1

To customize the lock path, edit the cron file's `LOCK_FILE` variable or the
systemd service's `ExecStart` line.

### Logging

GC output goes to:
- **systemd**: journald (`journalctl -u magpie-gc.service`)
- **cron**: syslog via `logger` (`grep magpie-gc /var/log/syslog`)

For JSON-formatted logs (useful for log aggregation):

```bash
# Direct execution
magpie-ctl gc --json-output

# Or configure MAGPIE_LOG_FORMAT=json globally
```

## Customization

### Changing the Schedule

**systemd** - Edit `/etc/systemd/system/magpie-gc.timer`:

```ini
[Timer]
# Every 6 hours (at 00:00, 06:00, 12:00, 18:00)
OnCalendar=*-*-* 0/6:00:00

# Weekly on Sunday at 3 AM
OnCalendar=Sun *-*-* 03:00:00

# First day of each month at midnight
OnCalendar=*-*-01 00:00:00
```

Then reload: `systemctl daemon-reload`

**cron** - Edit `/etc/cron.d/magpie-gc`:

```bash
# Every 6 hours
0 */6 * * * root flock -n ...

# Weekly on Sunday at 3 AM
0 3 * * 0 root flock -n ...
```

### Non-Docker Deployments

If running Magpie directly (not via Docker Compose), update the ExecStart:

**systemd**:
```ini
ExecStart=/usr/local/bin/magpie-ctl gc --quiet
```

**cron**:
```bash
0 2 * * * root flock -n /var/run/magpie-gc.lock /usr/local/bin/magpie-ctl gc --quiet 2>&1 | logger -t magpie-gc
```

### Custom Storage Path

If using a non-default storage path:

```bash
# Environment variable
MAGPIE_STORAGE_PATH=/mnt/artifacts

# Or in systemd service
Environment=MAGPIE_STORAGE_PATH=/mnt/artifacts
```

## Monitoring

### Check Timer Status (systemd)

```bash
# List all timers
systemctl list-timers

# Check specific timer
systemctl status magpie-gc.timer

# View last run and next scheduled
systemctl list-timers magpie-gc.timer --all
```

### View GC Logs

```bash
# systemd
journalctl -u magpie-gc.service
journalctl -u magpie-gc.service --since "1 hour ago"
journalctl -u magpie-gc.service -f  # Follow live

# cron
grep magpie-gc /var/log/syslog
tail -f /var/log/syslog | grep magpie-gc
```

### Manual GC Run

```bash
# Via systemd
sudo systemctl start magpie-gc.service

# Direct (Docker)
docker compose exec magpie magpie-ctl gc

# Direct (non-Docker)
magpie-ctl gc

# Dry run (preview only)
docker compose exec magpie magpie-ctl gc --dry-run
```

## Integrity Verification (Scrub) Scheduling

`magpie-ctl verify` re-reads stored blobs and compares them against the SHA-256
recorded in their metadata at upload time, detecting bit-rot or tampering of
artifacts such as OpenVPN CA/cert/key material. Nothing else in Magpie re-checks
stored bytes, so a periodic scrub is the only detection for silent corruption.

### Quick Start (systemd)

```bash
sudo cp systemd/magpie-verify.service /etc/systemd/system/
sudo cp systemd/magpie-verify.timer /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now magpie-verify.timer

systemctl list-timers magpie-verify.timer
```

Default schedule: weekly on Sunday at 4:00 AM (after the nightly GC run) with up
to 30 minutes random delay.

### Quick Start (cron)

```bash
sudo cp cron/magpie-verify /etc/cron.d/
sudo chmod 644 /etc/cron.d/magpie-verify
```

The cron file ships two schedules: a weekly full scrub and a daily scoped scrub
of crypto material (`--path openvpn`). Comment out whichever does not apply.
The shipped entries do not pipe into `logger`, so cron mails their output and
sees the scrub's exit status; a piped variant that preserves the status via
`bash -o pipefail -c` is included as a commented alternative.

### Bounding the Work

A full scrub re-reads every byte in storage, so bound scheduled runs:

```bash
magpie-ctl verify --path openvpn      # Only artifacts under a path prefix
magpie-ctl verify --limit 500         # At most 500 blobs
magpie-ctl verify --max-bytes 10737418240   # Byte budget (10 GiB)
```

`--limit`/`--max-bytes` always start at the beginning of the store and keep no
cursor, so repeated bounded runs re-verify the same head of the store instead of
rolling forward. Use them to cap the cost of a smoke check; to cover everything,
split the store with `--path` scopes or schedule an unbounded full scrub.

A common split is a daily scoped scrub of crypto material plus a weekly full
scrub off-peak.

### Locking

Verification uses its own lock file (`/var/run/magpie-verify.lock`) so scrubs
never overlap. Every shipped scrub command also takes the GC lock
(`/var/run/magpie-gc.lock`), and this is a correctness requirement, not just a
disk-I/O guard: GC unlinks a blob before its metadata sidecar, so a scrub that
walks an artifact mid-collection can see a sidecar whose blob is already gone and
report it as a missing blob (exit `3`). Verification re-reads an artifact's
records before reporting a missing blob, which closes most of that window, but
only the lock rules it out. Keep both `flock` calls.

Deletions through the API (`magpie delete`, retention pruning triggered by the
server) do not take the host lock. If a scrub reports missing blobs while
artifacts were being deleted, re-run it before treating the finding as data loss.

### Exit Codes and Alerting

| Code | Meaning |
| ---- | ------- |
| 0 | Every verified blob matched its recorded hash |
| 1 | Operational error (unreadable storage, invalid `--path`, corrupt metadata sidecar) |
| 3 | A referenced blob or a metadata sidecar is missing |
| 5 | Content mismatch: stored bytes do not match the recorded SHA-256 |

Alert on any non-zero exit, and page on `5` — it means an artifact is damaged or
tampered with and should be restored from backup rather than re-uploaded over.
For scraping, `magpie-ctl --format json verify` emits per-issue detail and full
counters and still exits non-zero. See [../docs/monitoring.md](../docs/monitoring.md).

```bash
# systemd
journalctl -u magpie-verify.service
systemctl status magpie-verify.service   # Non-zero exit shows as failed

# cron (mailed to the crontab owner, or syslog if using the piped alternative)
grep magpie-verify /var/log/syslog
```

## Troubleshooting

### GC Not Running

1. Check timer is enabled: `systemctl is-enabled magpie-gc.timer`
2. Check timer is active: `systemctl is-active magpie-gc.timer`
3. Check for lock file: `ls -la /var/run/magpie-gc.lock`
4. Check service logs: `journalctl -u magpie-gc.service -n 50`

### Lock File Issues

With `flock`-based locking (used in both systemd and cron), stale locks from
crashed processes are automatically handled. However, if you need to manually
verify or clean up:

```bash
# Check if GC is actually running
pgrep -f "magpie-ctl gc"

# View lock file (if curious - no cleanup needed with flock)
ls -la /var/run/magpie-gc.lock

# Force-stop a stuck GC process (only if truly stuck)
pkill -f "magpie-ctl gc"
```

Note: Unlike the old `ConditionPathExists` approach, `flock` automatically
releases locks when processes exit (even on crash), so manual lock file cleanup
is not needed.

### Permission Errors

Ensure the service user can:
- Read the storage directory
- Write to the lock file directory (`/var/run`)
- Execute Docker commands (if using Docker)

For Docker deployments, the user running the cron/systemd service needs access
to the Docker socket. By default, the service runs as root. To run as a
non-root user:

1. Add the user to the `docker` group: `sudo usermod -aG docker <username>`
2. Uncomment and set `User=<username>` in the systemd service file
3. Ensure the lock file directory is writable by that user

**Security Hardening Limitations**

The systemd service includes security hardening (`ProtectHome=true`,
`ProtectSystem=strict`) which may prevent access to `MAGPIE_DATA_DIR` if
configured to paths in `/home` or other protected locations.

If you encounter permission errors with a custom data directory:

1. **Recommended:** Use a non-protected path like `/opt/magpie/data` or `/var/lib/magpie`
2. **Alternative:** Adjust security settings in the systemd service:
   - Set `ProtectHome=false` if DATA_DIR is in `/home`
   - Add `ReadWritePaths=/path/to/your/data` for other protected paths
3. **Alternative:** For non-Docker deployments, explicitly add your data path:
   ```ini
   ReadWritePaths=/var/run /custom/path/to/data
   ```

Note: For Docker deployments, the container already has access to the mounted
data directory, so this limitation typically only affects direct (non-Docker)
execution.

---

*Note: This documentation was generated with AI assistance (Claude Code w/ Opus 4.5).*
