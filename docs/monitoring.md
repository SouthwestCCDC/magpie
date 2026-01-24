# Monitoring

This document covers basic monitoring for Magpie deployments.

## Health Check Endpoints

### GET /health

Public health check endpoint (no authentication required). Returns HTTP 200 with:

```json
{"status": "ok"}
```

Implementation: `src/magpie/server/app.py` line 57-60.

Used by Docker health checks (`docker-compose.yml`, `docker-compose.prod.yml`) and container orchestration.

### GET /api/v1/status

Server status endpoint with storage statistics (requires admin token). Returns HTTP 200 with:

```json
{
  "status": "ok",
  "version": "1.2.3",
  "storage": {
    "total_size_bytes": 1234567890,
    "artifact_count": 42,
    "blob_count": 156
  }
}
```

Implementation: `src/magpie/server/routes/status.py`.

Note: This endpoint walks the entire storage tree using `rglob`, which can be slow for large deployments. Call sparingly in production.

### CLI Status Command

The `magpie status` command queries `/api/v1/status` and displays formatted output:

```bash
$ magpie status
Server:    https://magpie.example.com
Status:    OK
Version:   1.2.3
Storage:   1.15 GiB used
Artifacts: 42 total
Blobs:     156 total
```

Requires admin token via `--token` or `MAGPIE_TOKEN`.

Implementation: `src/magpie/cli/commands/status.py`.

## Logging

### Log Format

Magpie uses structured logging (via `structlog`) with two output modes:

- `MAGPIE_LOG_FORMAT=json` (default): JSON logs for production/aggregation
- `MAGPIE_LOG_FORMAT=console`: Human-readable logs for development

Implementation: `src/magpie/logging_config.py`.

### Log Level

Controlled by `MAGPIE_DEBUG`:

- `MAGPIE_DEBUG=true`: DEBUG level (verbose)
- `MAGPIE_DEBUG=false` (default): INFO level

Implementation: `src/magpie/logging_config.py` line 74.

### Log Output

Logs are written to stderr (stdout is reserved for program output like JSON responses).

Implementation: `src/magpie/logging_config.py` line 69-76.

### Request Correlation

Every HTTP request gets a unique `request_id` that appears in all logs during that request. The ID is also returned in the `X-Request-ID` response header.

Example log entry:

```json
{
  "timestamp": "2026-01-17T00:29:09.772480Z",
  "level": "info",
  "logger": "magpie.server.middleware",
  "event": "request_complete",
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "method": "GET",
  "path": "/api/v1/artifacts/example/file.txt",
  "status_code": 200,
  "duration_ms": 12.34
}
```

Implementation: `src/magpie/server/middleware.py`.

### Key Log Events

The middleware logs these events for all requests:

- `request_start`: Request received (method, path, client_host)
- `request_complete`: Request succeeded (method, path, status_code, duration_ms)
- `request_failed`: Request failed with exception (method, path, duration_ms, error)

Implementation: `src/magpie/server/middleware.py` lines 49-54, 64-70, 82-89.

See `docs/structured-logging.md` for full details on log format and fields.

## Garbage Collection Monitoring

### Systemd Timer

For deployments using `deployment/systemd/magpie-gc.timer`:

```bash
# Check timer status and next scheduled run
systemctl status magpie-gc.timer

# List all timers including magpie-gc
systemctl list-timers magpie-gc.timer

# View GC service logs
journalctl -u magpie-gc.service
```

Default schedule: Daily at 2:00 AM (with 30-minute randomization window).

Implementation: `deployment/systemd/magpie-gc.timer`.

### GC Lock File

GC uses flock-based locking to prevent concurrent runs:

- Lock file: `/var/run/magpie-gc.lock` (configurable via `MAGPIE_GC_LOCK_PATH`)
- Lock is automatically released when GC completes or crashes (flock handles stale locks)

Implementation: `deployment/systemd/magpie-gc.service` line 33, `src/magpie/config.py` line 35.

## Observability Integrations

### Sentry

Error tracking via Sentry (optional):

- Enable: Set `MAGPIE_SENTRY_DSN` to your Sentry DSN
- Configuration: `src/magpie/server/observability.py` lines 13-36
- Environment: Automatically set to "development" (debug=true) or "production" (debug=false)
- Trace sampling: 100% in development, 10% in production

### OpenTelemetry

Distributed tracing via OpenTelemetry (optional):

- Enable: Set `MAGPIE_OTEL_ENABLED=true`
- Endpoint: Set `MAGPIE_OTEL_ENDPOINT` (e.g., `http://localhost:4317` for OTLP gRPC)
- Service name: Configure via `MAGPIE_OTEL_SERVICE_NAME` (default: "magpie")
- Configuration: `src/magpie/server/observability.py` lines 39-72

When OTEL is enabled, log entries include `trace_id` and `span_id` fields for correlation.

Implementation: `src/magpie/logging_config.py` lines 79-104.

## Monitoring Recommendations

### Critical Metrics

Based on the available instrumentation:

1. **Health endpoint availability**: Monitor `GET /health` returns HTTP 200
2. **Disk space**: Monitor filesystem where `MAGPIE_STORAGE_PATH` is mounted
   - Storage usage is reported by `/api/v1/status` (slow for large deployments)
   - Consider filesystem-level monitoring (df, node_exporter, etc.)
3. **Error rates**: Monitor log events with `"level": "error"` or `"event": "request_failed"`
4. **Request latency**: Monitor `duration_ms` field in `request_complete` events
5. **GC timer failures**: Monitor `systemctl status magpie-gc.timer` and GC service logs

### Alert Thresholds

Suggested thresholds (adjust based on deployment):

- Disk space < 20% free on storage volume
- Health check failures (3+ consecutive failures)
- Error rate > 5% of requests over 5-minute window
- GC timer not running (check `systemctl list-timers`)
- Request latency p95 > 5 seconds (baseline depends on artifact sizes)

Note: These thresholds are recommendations and have not been tested in production. Adjust based on observed behavior.

---

*This documentation was generated with AI assistance (Claude Code w/ Sonnet 4.5).*
