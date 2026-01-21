# Monitoring Guide

This guide provides basic monitoring recommendations for Magpie in production.

## Quick Reference

| Check | Endpoint/Command | Critical Threshold |
|-------|-----------------|-------------------|
| Server health | `GET /health` | HTTP 200 expected |
| Server status | `GET /api/v1/status` | HTTP 200 expected |
| CLI status | `magpie status` | Exit code 0 expected |
| Disk space | Check `/health` response time or filesystem | <20% free space remaining |
| Error rate | Sentry dashboard or logs | Sustained 5xx errors |

## Health Check Endpoints

### Basic Health Check

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

The `/health` endpoint is a simple liveness check that returns HTTP 200 if the server process is running and responding to requests.

**Use for:**
- Load balancer health checks
- Kubernetes liveness probes
- Simple uptime monitoring

### Detailed Status Check

```bash
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/status
```

Example response:
```json
{
  "status": "ok",
  "version": "0.1.0",
  "auth": {
    "valid": true,
    "scope": "admin",
    "name": "monitoring-token"
  },
  "storage": {
    "total_size_bytes": 1073741824,
    "artifact_count": 42,
    "blob_count": 156
  }
}
```

**Use for:**
- Detailed health checks
- Storage capacity monitoring
- Token validation verification
- Version tracking

**Performance note:** This endpoint walks the entire storage tree and can be slow on large deployments. Call sparingly in production (e.g., every 5-15 minutes, not every second).

### CLI Status Command

```bash
magpie status
```

Example output:
```
Server Status
Version: 0.1.0
Status: ok

Authentication
Token: monitoring-token
Scope: admin
Valid: true

Storage Statistics
Artifacts: 42
Blobs: 156
Total Size: 1.00 GB
```

**Use for:**
- Manual health checks
- Scripted monitoring without API calls
- Quick status verification

## Key Metrics to Monitor

### 1. Disk Space

**Critical:** Magpie stores all artifacts on disk. Running out of space will cause upload failures.

**Monitor:**
- Filesystem where `MAGPIE_STORAGE_PATH` is mounted
- Alert when free space drops below 20%
- Track `total_size_bytes` from `/api/v1/status` over time

**Check disk usage:**
```bash
# Check filesystem
df -h /data/artifacts

# Or via API
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/status | jq '.storage.total_size_bytes'
```

**Remediation:**
- Run garbage collection: `magpie-ctl gc` (with appropriate flags)
- Remove old artifacts or flush unused tags
- Expand storage capacity

### 2. HTTP Health Status

**Critical:** If the health endpoint fails, the server is down or unresponsive.

**Monitor:**
- `/health` endpoint should return HTTP 200
- Response time should be <100ms (baseline for simple health check)
- Failed health checks indicate process crash or network issues

**Alert conditions:**
- 3+ consecutive health check failures
- Response time >1 second (may indicate resource exhaustion)

### 3. Error Rate

**Monitor:**
- 5xx server errors in logs or Sentry
- Authentication failures (repeated 401/403 responses)
- Storage write failures

**Alert conditions:**
- Any sustained 5xx error rate (>1% of requests)
- Sudden spike in authentication failures (possible token misconfiguration)
- Storage write errors (possible disk full or permissions issue)

**Check logs:**
```bash
# With structured logging (JSON format)
docker compose logs magpie-api | grep '"level":"error"'

# Count errors in last hour
docker compose logs --since 1h magpie-api | grep -c '"level":"error"'
```

### 4. Request Latency

**Monitor:**
- `/health` response time (baseline <100ms)
- `/api/v1/status` response time (varies with artifact count)
- Upload endpoint response time (varies with file size)

**Alert conditions:**
- `/health` consistently >500ms
- `/api/v1/status` >10 seconds (may indicate storage performance issue)

**Note:** Upload latency depends on file size and is not a reliable health indicator by itself.

## Log Analysis Patterns

### Finding Recent Errors

```bash
# Recent errors (JSON logs)
docker compose logs --since 10m magpie-api | grep '"level":"error"'

# Specific error type
docker compose logs magpie-api | grep '"event":"upload_failed"'
```

### Authentication Issues

```bash
# Failed auth attempts
docker compose logs magpie-api | grep '"event":"auth_validation_failed"'

# Check what tokens are failing
docker compose logs magpie-api | grep 'auth_validation_failed' | jq '.reason'
```

### Storage Operations

```bash
# Upload activity
docker compose logs magpie-api | grep '"event":"upload_complete"'

# Garbage collection results
docker compose logs magpie-api | grep '"event":"gc_complete"'

# Check disk space reclaimed
docker compose logs magpie-api | grep 'gc_complete' | jq '.space_reclaimed_bytes'
```

### Performance Tracking

```bash
# Find slow uploads (>5 seconds)
docker compose logs magpie-api | grep 'upload_complete' | jq 'select(.duration_ms > 5000)'

# Average upload duration (requires jq)
docker compose logs magpie-api | grep 'upload_complete' | jq '.duration_ms' | awk '{sum+=$1; count++} END {print sum/count}'
```

## Alert Recommendations

### Critical Alerts (Immediate Action Required)

| Condition | Alert Threshold | Action |
|-----------|----------------|--------|
| Service down | `/health` returns non-200 for 3+ minutes | Restart service, check logs |
| Disk space critical | <10% free space | Run GC or expand storage immediately |
| Sustained errors | >5% of requests are 5xx for 5+ minutes | Check logs, investigate errors |

### Warning Alerts (Investigate Soon)

| Condition | Alert Threshold | Action |
|-----------|----------------|--------|
| Disk space low | <20% free space | Plan GC run or storage expansion |
| Slow health checks | `/health` >500ms for 5+ minutes | Check system resources (CPU, disk I/O) |
| Auth failures | >10 failed auth attempts in 5 minutes | Check token configuration, possible attack |
| Storage stats slow | `/api/v1/status` >30 seconds | Artifact count may be too large, consider caching |

### Informational (Track Trends)

| Metric | Recommended Frequency | Purpose |
|--------|----------------------|---------|
| Storage growth | Daily | Capacity planning |
| Artifact count | Daily | Usage trends |
| Upload patterns | Hourly | Identify peak usage times |
| GC effectiveness | After each GC run | Verify space reclamation |

## Integration with Monitoring Tools

### Prometheus / Grafana

Magpie does not currently expose Prometheus metrics. Monitor via HTTP checks and log scraping.

Example Prometheus scrape config for health checks:
```yaml
- job_name: 'magpie-health'
  metrics_path: /health
  static_configs:
    - targets: ['magpie.example.com:8000']
```

**Future enhancement:** Add `/metrics` endpoint with Prometheus exporter. See [issue #256](https://github.com/SouthwestCCDC/magpie/issues/256) (if created).

### Loki / Log Aggregation

Magpie's structured JSON logs integrate seamlessly with Loki, Elasticsearch, or similar log aggregators.

Example Loki query:
```logql
# All errors in last hour
{service="magpie"} | json | level="error"

# Uploads taking >5 seconds
{service="magpie"} | json | event="upload_complete" | duration_ms > 5000

# GC space reclaimed
{service="magpie"} | json | event="gc_complete" | line_format "{{.space_reclaimed_bytes}}"
```

See [structured-logging.md](./structured-logging.md) for detailed log event documentation.

### Sentry / Error Tracking

Sentry automatically captures 5xx server errors. Configure with `MAGPIE_SENTRY_DSN` environment variable.

See [sentry-verification.md](./sentry-verification.md) for setup instructions.

## Example Monitoring Script

```bash
#!/bin/bash
# Basic Magpie health check script
# Exit code 0 = healthy, 1 = unhealthy

MAGPIE_URL="${MAGPIE_URL:-http://localhost:8000}"
TIMEOUT=5

# Check health endpoint
if ! curl -sf --max-time "$TIMEOUT" "$MAGPIE_URL/health" > /dev/null; then
    echo "ERROR: Health check failed"
    exit 1
fi

# Check disk space (assuming /data/artifacts mount)
DISK_USAGE=$(df /data/artifacts | awk 'NR==2 {print int($5)}')
if [ "$DISK_USAGE" -gt 80 ]; then
    echo "WARNING: Disk usage at ${DISK_USAGE}%"
    exit 1
fi

echo "OK: Magpie healthy, disk at ${DISK_USAGE}%"
exit 0
```

## Troubleshooting Common Issues

### Health Check Failing

1. Check if process is running: `docker compose ps magpie-api`
2. Check logs: `docker compose logs --tail 100 magpie-api`
3. Verify port binding: `netstat -tlnp | grep 8000`
4. Check resource limits: `docker stats magpie-api`

### Slow Response Times

1. Check disk I/O: `iostat -x 5`
2. Check `/api/v1/status` response time (may be slow if many artifacts)
3. Review recent uploads (large files can cause temporary slowness)
4. Check for concurrent GC operations

### Disk Space Filling Up

1. Check current usage: `magpie status`
2. Review artifact count and blob count
3. Run garbage collection: `magpie-ctl gc --dry-run` (preview first)
4. Flush old tags: `magpie tag flush <old-tag> --dry-run`
5. Consider storage expansion if legitimate growth

### Authentication Errors

1. Verify token with: `magpie-ctl tokens list`
2. Check token scope matches operation (read vs write vs admin)
3. Review auth logs: `docker compose logs magpie-api | grep auth_validation`
4. Regenerate token if compromised: `magpie-ctl tokens delete <name>` then `magpie-ctl tokens create <name> <scope>`

## References

- [Structured Logging](./structured-logging.md) - Log event details
- [Sentry Verification](./sentry-verification.md) - Error tracking setup
- [User Guide](./user-guide.md) - CLI commands and API usage
- [Backup & Restore](./backup-restore.md) - Disaster recovery procedures

---

*This documentation was generated with AI assistance (Claude Code w/ Opus 4.5).*
