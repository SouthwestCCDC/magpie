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

Configure via environment variable:

```bash
# In .env file or shell environment
MAGPIE_RETENTION_DAYS=30
```

Or via command-line override:

```bash
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

The lock file (`MAGPIE_GC_LOCK_PATH`) prevents concurrent GC runs, which could
cause race conditions or excessive resource usage.

Default path: `/var/run/magpie-gc.lock`

The systemd service uses `ConditionPathExists` to check the lock file before
starting. The cron configuration uses `flock -n` for the same purpose.

If GC is already running:
- systemd: Service fails immediately (check `systemctl status magpie-gc`)
- cron: `flock` exits silently with status 1

To customize the lock path:

```bash
# In .env file
MAGPIE_GC_LOCK_PATH=/var/lock/magpie-gc.lock
```

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
# Every 6 hours
OnCalendar=*-*-* 00/6:00:00

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
0 2 * * * root flock -n $MAGPIE_GC_LOCK_PATH /usr/local/bin/magpie-ctl gc --quiet 2>&1 | logger -t magpie-gc
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

## Troubleshooting

### GC Not Running

1. Check timer is enabled: `systemctl is-enabled magpie-gc.timer`
2. Check timer is active: `systemctl is-active magpie-gc.timer`
3. Check for lock file: `ls -la /var/run/magpie-gc.lock`
4. Check service logs: `journalctl -u magpie-gc.service -n 50`

### Lock File Stuck

If GC was interrupted, the lock file may remain:

```bash
# Check if GC is actually running
pgrep -f "magpie-ctl gc"

# If not running, remove stale lock
sudo rm /var/run/magpie-gc.lock
```

### Permission Errors

Ensure the service user can:
- Read the storage directory
- Write to the lock file path
- Execute docker commands (if using Docker)

For Docker deployments, the user running the cron/systemd service needs access
to the Docker socket.

---

*Note: This documentation was generated with AI assistance (Claude Code w/ Opus 4.5).*
