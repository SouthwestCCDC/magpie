# Monitoring & Observability

## Health Checks

**Public health endpoint** (`GET /health`):
```bash
curl https://magpie.example.com/health
```
Returns `{"status": "ok", "version": "x.y.z"}` if operational. Used by Docker health checks and load balancers.
The bundled image checks `/health` through Caddy to verify that both Caddy and FastAPI
respond. Use this rather than the storage-walking admin status endpoint for frequent probes.

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
- The canonical Compose file sets `MAGPIE_DEBUG=false` (INFO). Setting it in `.env` does
  not change the container's logging level.

**Standard fields:** FastAPI's structured logs include timestamp, level, event, and logger.
Request logs also include `request_id`. Caddy logs separately.

**Key events:**
- `upload_complete` - artifact_path, hash_ref, size_bytes, duration_ms, is_duplicate
- `tag_created`, `tag_removed` - tag_name, artifact_path; `tag_flushed` - tag_name and totals
- `gc_complete` - artifacts_scanned, blobs_deleted, space_reclaimed_bytes
- `auth_validation_success` - token_name, scope; `auth_validation_failed` - reason

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

## Error Tracking (Sentry)

Optional integration for unhandled exceptions and performance monitoring.

**Setup:**
1. Create Sentry project at [sentry.io](https://sentry.io) (free tier available)
2. Get DSN from project settings
3. Set the DSN in your Compose environment file (`.env`, or `<install dir>/etc/.env`
   for an installer-managed host):
```bash
MAGPIE_SENTRY_DSN="https://<key>@<org>.ingest.sentry.io/<project>"
MAGPIE_SENTRY_TRACES_SAMPLE_RATE=0.1
```
4. Recreate the Compose container to apply the new environment, or restart
   `magpie.service` for an installer-managed host.

**What's captured:**
- 5xx errors and FastAPI exceptions
- Performance traces sampled at `MAGPIE_SENTRY_TRACES_SAMPLE_RATE` (default `1.0`
  regardless of debug mode; `0.1` samples 10%)
- Request context and stack traces
- Not captured: 4xx errors, handled exceptions, PII

**Verification:**
```bash
docker compose logs magpie | grep -i sentry
```
Check Sentry dashboard for stack traces and environment tags.

## Distributed Tracing (OpenTelemetry)

To enable tracing and export spans, set these in the same environment file:
```bash
MAGPIE_OTEL_ENABLED=true
MAGPIE_OTEL_ENDPOINT=http://otel-collector:4317
```

Use a reachable OTLP gRPC collector. FastAPI logs include `trace_id` and `span_id`
when a span is active.

## GC Monitoring

See [user-guide.md](user-guide.md) for garbage collection operations and retention configuration.

---

*(AI-generated via Claude Code w/ Sonnet 4.5; v0.2.1 docs audit via Devin)*
