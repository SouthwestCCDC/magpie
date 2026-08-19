# Monitoring & Observability

## Health Checks

**Public health endpoint** (`GET /health`):
```bash
curl https://magpie.example.com/health
```
Returns `{"status": "ok", "version": "x.y.z"}` if operational. Used by Docker health checks and load balancers.

**Admin status endpoint** (`GET /api/v1/status`, admin token required):
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

**CLI status command:**
```bash
magpie status    # Shows storage stats (requires admin token)
```

## Logging

Magpie uses structured logging (`structlog`) for machine-readable output.

**Configuration:**
- `MAGPIE_LOG_FORMAT=json` (default) - JSON for aggregation systems
- `MAGPIE_LOG_FORMAT=console` - Human-readable for development
- `MAGPIE_DEBUG=true` - DEBUG level (verbose)
- `MAGPIE_DEBUG=false` (default) - INFO level

**Standard fields:** All logs include timestamp, level, event, logger, and request_id (for HTTP requests).

**Key events:**
- `upload_complete` - artifact_path, hash_ref, size_bytes, duration_ms, is_duplicate
- `tag_created`, `tag_removed`, `tag_flushed` - tag_name, artifact_path
- `gc_complete` - artifacts_scanned, blobs_deleted, space_reclaimed_bytes
- `verify_complete` - artifacts_scanned, blobs_scanned, bytes_read, ok, mismatched, missing_blob, missing_metadata, corrupt_metadata, errors, stopped_early
- `verify_issue` - artifact_path, blob_ref, status, expected_hash, actual_hash (logged at WARNING for each finding)
- `auth_validation_success`, `auth_validation_failed` - token_name, scope

**Example JSON log entry:**
```json
{
  "timestamp": "2026-01-17T00:29:09.772480Z",
  "level": "info",
  "event": "upload_complete",
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "artifact_path": "images/ubuntu",
  "hash_ref": "abc12345",
  "size_bytes": 51200,
  "duration_ms": 45.12,
  "is_duplicate": false
}
```

**Log aggregation:** JSON format works with Loki, Elasticsearch, Datadog, and New Relic.

## Critical Metrics

Alert on these conditions:
1. Health endpoint returns non-200
2. Disk space < 20% free on `MAGPIE_STORAGE_PATH` volume
3. Error rate > 5% of requests
4. Request latency p95 > 5 seconds
5. Health check failures (3+ consecutive)
6. `magpie-ctl verify` exits non-zero, or a `verify_complete` event reports `mismatched > 0` (page immediately: stored bytes no longer match their recorded hash)

## Error Tracking (Sentry)

Optional integration for unhandled exceptions and performance monitoring.

**Setup:**
1. Create Sentry project at [sentry.io](https://sentry.io) (free tier available)
2. Get DSN from project settings
3. Set environment variable:
```bash
export MAGPIE_SENTRY_DSN="https://<key>@<org>.ingest.sentry.io/<project>"
```
4. Restart server

**What's captured:**
- 5xx errors and FastAPI exceptions
- Performance traces (100% in debug, 10% in production)
- Request context and stack traces
- Not captured: 4xx errors, handled exceptions, PII

**Verification:**
```bash
docker compose logs magpie | grep -i sentry
```
Check Sentry dashboard for stack traces and environment tags.

## Distributed Tracing (OpenTelemetry)

Enable distributed tracing across services:
```bash
MAGPIE_OTEL_ENABLED=true
MAGPIE_OTEL_ENDPOINT=http://otel-collector:4317
```

Logs include `trace_id` and `span_id` for correlation with other services.

## GC Monitoring

See [user-guide.md](user-guide.md) for garbage collection operations and retention configuration.

## Integrity Monitoring (Scrub)

`magpie-ctl verify` re-reads stored blobs and compares them to the SHA-256 recorded at upload time. Nothing else in Magpie re-checks stored bytes, so a scheduled scrub is the only detection for bit-rot or tampering of artifacts like OpenVPN CA/cert/key material.

**Exit codes** (see [user-guide.md](user-guide.md) for the full table): `0` clean, `1` operational error, `3` missing blob/metadata, `5` content mismatch. A cron job or systemd timer that logs stderr and alerts on non-zero status is sufficient; alert separately on `5`, which indicates damaged or tampered content rather than a bookkeeping problem.

**Scraping results:**
```bash
magpie-ctl --format json verify --path openvpn
```

```json
{
  "status": "ok",
  "data": {
    "path_prefix": "openvpn",
    "artifacts_scanned": 3,
    "blobs_scanned": 7,
    "bytes_read": 20481,
    "ok": 6,
    "mismatched": 1,
    "missing_blob": 0,
    "missing_metadata": 0,
    "corrupt_metadata": 0,
    "errors": 0,
    "stopped_early": false,
    "issues": [
      {
        "artifact_path": "openvpn/ca",
        "blob_ref": "abc12345",
        "status": "mismatch",
        "message": "Stored content does not match recorded SHA-256",
        "expected_hash": "...",
        "actual_hash": "...",
        "size_bytes": 2048
      }
    ]
  }
}
```

The command still exits non-zero when JSON output is requested, so monitoring can key on either the exit code or the counters. Only the result document goes to stdout — `verify_complete` and `verify_issue` log events go to stderr — so stdout can be piped straight into a JSON parser. `stopped_early` is `true` when `--limit`/`--max-bytes` cut the run short, which means a clean result covers only the blobs actually scanned; bounded runs always start at the beginning of the store, so they repeat the same head rather than rolling forward.

**Scheduling:** a full scrub re-reads every byte, so run it off-peak and/or scope it. Verify crypto material often and everything else less frequently:

```bash
0 3 * * *  magpie-ctl verify --path openvpn --quiet   # Daily, crypto material only
0 4 * * 0  magpie-ctl verify --quiet                  # Weekly full scrub
```

Do not pipe these into `logger`: in a shell pipeline the job's exit status becomes the status of the last command, which discards the scrub result. Let cron capture the output, or wrap the pipeline in `bash -o pipefail -c`.

**Do not overlap a scrub with GC.** GC unlinks a blob before its metadata sidecar, so a scrub that walks an artifact mid-collection can report a collected blob as missing (exit `3`). Verification re-reads an artifact's records before reporting a missing blob, which closes most of that window; taking the GC lock (`/var/run/magpie-gc.lock`, as the shipped units in [../deployment/](../deployment/) do) rules it out. Deletions through the API do not take that lock, so re-run a scrub that reports missing blobs during heavy deletion activity before treating it as data loss.

Storage the scrub cannot read is a finding, not a skip: an unreadable directory or blob is counted under `errors` and reported as a `verify_issue` with `status: "error"` (exit `1`), so a permission or hardware problem cannot masquerade as a clean store.

See [../deployment/README.md](../deployment/README.md) for ready-made cron and systemd timer units, including locking that keeps a scrub from overlapping with GC.

---

*(AI-generated via Claude Code w/ Sonnet 4.5)*
