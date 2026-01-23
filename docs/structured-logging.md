# Structured Logging with structlog

This document describes the structured logging implementation in Magpie using `structlog`.

## Overview

Magpie uses structured logging to emit machine-readable JSON logs (or human-readable console logs for development). All log entries include standardized fields and support correlation IDs for request tracing.

## Configuration

Logging is configured via the `MAGPIE_LOG_FORMAT` environment variable:

- `json` (default): JSON-formatted logs suitable for production and log aggregators (Loki, Elasticsearch, etc.)
- `console`: Human-readable console output for local development

### Log Level

The log level is controlled by the `MAGPIE_DEBUG` environment variable:

- `debug=true`: Sets log level to DEBUG
- `debug=false` (default): Sets log level to INFO

## Standard Fields

All log entries include:

- `timestamp`: ISO 8601 timestamp (UTC)
- `level`: Log level (info, warning, error, debug)
- `logger`: Module name that generated the log
- `event`: The log event name (e.g., "upload_complete", "gc_complete")

## Request Correlation

HTTP requests automatically include a `request_id` field in all logs generated during request processing. The request ID is also returned in the `X-Request-ID` response header.

Example:
```json
{
  "timestamp": "2026-01-17T00:29:09.772480Z",
  "level": "info",
  "logger": "magpie.server.routes.upload",
  "event": "upload_complete",
  "request_id": "req_abc123xyz",
  "artifact_path": "test/file.txt",
  "hash": "def67890",
  "duration_ms": 123.45
}
```

## OpenTelemetry Integration

When OpenTelemetry is enabled (`MAGPIE_OTEL_ENABLED=true`), logs automatically include:

- `trace_id`: Distributed trace ID (32 hex chars)
- `span_id`: Current span ID (16 hex chars)

This allows correlation between logs and distributed traces.

## Key Log Events

### Upload Operations

Event: `upload_complete`

Fields:
- `artifact_path`: Path where artifact was stored
- `hash`: Full SHA-256 hash
- `hash_ref`: Short hash reference (@abc12345)
- `size_bytes`: Size of uploaded file
- `duration_ms`: Upload duration in milliseconds
- `uploaded_by`: User who uploaded the artifact
- `is_duplicate`: Whether blob already existed
- `source_uri`: Optional source URI

Example:
```json
{
  "event": "upload_complete",
  "artifact_path": "images/infra/vrouter.qcow2",
  "hash": "abc12345def67890...",
  "hash_ref": "@abc12345",
  "size_bytes": 1073741824,
  "duration_ms": 4523.45,
  "uploaded_by": "admin",
  "is_duplicate": false,
  "source_uri": "https://example.com/vrouter.qcow2",
  "timestamp": "2026-01-17T00:29:09.770074Z",
  "level": "info"
}
```

### Tag Operations

Event: `tag_created`, `tag_removed`, `tag_flushed`

Fields:
- `operation`: Operation type (create_tag, remove_tag, flush_tag)
- `tag_name`: Name of the tag
- `artifact_path`: Artifact path (for create/remove)
- `hash_ref`: Hash being tagged (for create)
- `dry_run`: Whether operation was a dry run (for flush)
- `affected_count`: Number of artifacts affected (for flush)

Example:
```json
{
  "event": "tag_created",
  "operation": "create_tag",
  "tag_name": "stable",
  "artifact_path": "images/infra/vrouter.qcow2",
  "hash_ref": "@abc12345",
  "timestamp": "2026-01-17T00:29:09.770232Z",
  "level": "info"
}
```

### Garbage Collection

Event: `gc_complete`

Fields:
- `dry_run`: Whether GC was in dry-run mode
- `reconcile_only`: Whether GC only reconciled symlinks
- `artifacts_scanned`: Number of artifacts scanned
- `blobs_found`: Total blobs found
- `blobs_deleted`: Number of blobs deleted
- `space_reclaimed_bytes`: Bytes freed
- `symlinks_checked`: Number of symlinks checked
- `symlinks_fixed`: Number of symlinks fixed
- `items_removed`: Total items removed (dirs, files)

Example:
```json
{
  "event": "gc_complete",
  "dry_run": false,
  "reconcile_only": false,
  "artifacts_scanned": 42,
  "blobs_found": 156,
  "blobs_deleted": 8,
  "space_reclaimed_bytes": 524288000,
  "symlinks_checked": 42,
  "symlinks_fixed": 2,
  "items_removed": 3,
  "timestamp": "2026-01-17T00:29:09.770323Z",
  "level": "info"
}
```

### Authentication

Event: `auth_validation_success`, `auth_validation_failed`

Fields:
- `token_name`: Name of the validated token (on success)
- `scope`: Token scope (read, write, admin) (on success)
- `reason`: Failure reason (on failure)

Examples:
```json
{
  "event": "auth_validation_success",
  "token_name": "deployment-bot",
  "scope": "write",
  "timestamp": "2026-01-17T00:29:09.770483Z",
  "level": "info"
}
```

```json
{
  "event": "auth_validation_failed",
  "reason": "invalid_token",
  "timestamp": "2026-01-17T00:29:09.770387Z",
  "level": "warning"
}
```

## Using Structured Logging in Code

```python
import structlog

logger = structlog.get_logger()

# Basic logging with structured fields
logger.info(
    "operation_complete",
    field1="value1",
    field2=42,
    duration_ms=123.45
)

# Warning with context
logger.warning(
    "potential_issue",
    artifact_path=path,
    reason="validation_failed"
)

# Error with exception
try:
    risky_operation()
except Exception as e:
    logger.error(
        "operation_failed",
        error=str(e),
        exc_info=True  # Includes stack trace
    )
```

## Log Aggregation

JSON logs are designed for consumption by log aggregators:

- **Loki**: Use Promtail to ship logs, query by `event`, `request_id`, etc.
- **Elasticsearch**: Ingest JSON logs directly, create dashboards by operation
- **Datadog/New Relic**: Forward logs via agent, filter by structured fields

Example Loki query:

```logql
{service="magpie"} | json | event="upload_complete" | duration_ms > 5000
```

## Best Practices

1. **Use descriptive event names**: `upload_complete`, not `done`
2. **Include relevant context**: Always add fields like `artifact_path`, `hash_ref`
3. **Measure durations**: Log `duration_ms` for performance tracking
4. **Don't log secrets**: Never log tokens, passwords, or sensitive data
5. **Use appropriate levels**:
   - `debug`: Detailed diagnostic information
   - `info`: Normal operations and milestones
   - `warning`: Recoverable issues
   - `error`: Errors requiring attention

---

*This documentation was generated with AI assistance (Claude Code w/ Opus 4.5).*
