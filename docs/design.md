# Magpie Artifacts: Design Document

> **Note:** This document was developed with assistance from Claude Code based on analysis of the
> current artifacts infrastructure and iterative design discussions.

**Status:** Released
**Authors:** George (with Claude Code)
**Date:** January 2026
**Repository:** [SouthwestCCDC/magpie](https://github.com/SouthwestCCDC/magpie)

---

## Table of Contents

1. [Current State](#current-state)
2. [Problem Analysis](#problem-analysis)
3. [Requirements](#requirements)
4. [Proposed Design](#proposed-design)
5. [Resolved Design Questions](#resolved-design-questions)
6. [Future Work](#future-work)

---

## Current State

### Current Architecture

The artifacts server (`artifacts.swccdc.com`) is a Caddy-based file server hosted on the bootstrap
server (`bootstrap.infra.swccdc.com`). It serves files from local disk, synced from an S3 bucket.

```text
+-------------------------------------------+
|              S3 Bucket                    |
|    talon-artifact-bootstrap-bucket        |
+-------------------------------------------+
                    ^
                    | aws s3 sync (pull, every 5 min)
                    |
+-------------------------------------------+
|          Bootstrap Server                 |
|    bootstrap.infra.swccdc.com             |
|                                           |
|  Caddy container (artifacts.swccdc.com)   |
|  - Serves /data/artifacts                 |
|  - Basic auth (blackteam/blackteam)       |
|  - /public/* and /isos/* unauthenticated  |
+-------------------------------------------+
                    ^
                    | HTTPS GET
                    |
+-------------------------------------------+
|             Consumers                     |
|  - OpenNebula (VM images)                 |
|  - Ansible playbooks (game assets)        |
|  - VMs (root CA cert)                     |
+-------------------------------------------+
```

### Storage Hardware

The bootstrap server has:

- **1.6TB NVMe** - Boot drive
- **2x 1.8TB SATA SSDs** (Intel SSDSC2KB019T7) - Artifacts storage

Current mount configuration (from `infra-deployment/ansible/host_vars/10.3.3.100.yml`):

| Mount Point       | Device      | Size  | Current Use                  |
|-------------------|-------------|-------|------------------------------|
| `/data/artifacts` | `/dev/sda1` | 1.8TB | Synced from S3 (~168GB used) |
| `/data/scratch`   | `/dev/sdb1` | 1.8TB | Mounted but empty/unused     |

### Key Code Locations

| Component                  | Location                                                          | Description               |
|----------------------------|-------------------------------------------------------------------|---------------------------|
| Artifacts Ansible role     | `infra-deployment/ansible/roles/artifacts/`                       | Deploys Caddy, sync timer |
| Role defaults              | `infra-deployment/ansible/roles/artifacts/defaults/main.yml`      | Default config values     |
| Group vars                 | `infra-deployment/ansible/group_vars/artifacts/artifacts.yml`     | Actual config             |
| Host vars (storage)        | `infra-deployment/ansible/host_vars/10.3.3.100.yml`               | Disk mounts               |
| Caddyfile template         | `infra-deployment/ansible/roles/artifacts/templates/Caddyfile`    | Access control            |
| Sync service               | `infra-deployment/ansible/roles/artifacts/templates/sync.service` | S3 sync command           |
| Sync timer                 | `infra-deployment/ansible/roles/artifacts/templates/sync.timer`   | 5-minute schedule         |
| AWS IAM policy             | `infra-deployment/terraform/aws/policies/artifacts-sync.json`     | S3 permissions            |
| AWS IAM user               | `infra-deployment/terraform/aws/service_accounts.tf`              | `artifacts-sync` module   |
| ONE images using artifacts | `deployment/terraform/prod/opennebula_platform/images.tf`         | ~30 images from artifacts |

### Current S3 Bucket Structure

```text
talon-artifact-bootstrap-bucket/
+-- public/                 # Unauthenticated (certs, logos)
+-- iso/                    # VM images (~30 images, largest category)
+-- installers/             # Software packages
+-- 2024/, 2025/            # Per-year game assets
+-- _archive/               # Excluded from sync
```

### Current Versioning (Ad-hoc)

Images are currently "versioned" via filename conventions:

```text
vyos-equuleus-e25c7826.qcow2   # Git commit hash suffix
windows-2019-1493426a.qcow2    # Git commit hash suffix
pfsense-2.7.2-3a71a1f4.qcow2   # Version + hash
PA-VM-KVM-11.1.2.qcow2         # Upstream version only
```

Terraform references hardcode specific filenames. There's no "latest" concept - updating requires
changing Terraform and re-applying.

### How Images Get Published Today

**Packer builds** (e.g., VyOS in `deployment/packer/base-images/vyos/equuleus/Makefile`):

```bash
# Build locally
make output-vyos-equuleus

# Upload to S3 with git hash
aws --profile swccdc-deployment --region us-east-2 \
    s3 cp output-vyos-equuleus/vyos-equuleus.qcow2 \
    s3://talon-artifact-bootstrap-bucket/iso/vyos-equuleus-${GIT_HASH}.qcow2

# Wait up to 5 minutes for sync, or manually trigger
ssh bootstrap.infra.swccdc.com sudo systemctl start artifacts-sync.service
```

**Manual uploads:**

```bash
aws --profile swccdc-deployment s3 cp file.tar.gz s3://talon-artifact-bootstrap-bucket/2025/quals/
```

---

## Problem Analysis

### Ingestion Issues

| Issue                    | Impact                                |
|--------------------------|---------------------------------------|
| S3-only ingestion path   | Operators need AWS CLI + credentials  |
| No direct push to server | Can't bypass S3 for dev/temp images   |
| 5-minute sync delay      | New uploads not immediately available |
| No upload validation     | No checksums, no metadata             |

### Versioning Issues

| Issue                  | Impact                                               |
|------------------------|------------------------------------------------------|
| Filenames are versions | Changing version = changing all Terraform references |
| No "latest" pointers   | Can't follow current version automatically           |
| No rollback mechanism  | Must know exact filename to revert                   |
| Ad-hoc naming          | Inconsistent patterns across image types             |

### Lifecycle Issues

| Issue                 | Impact                                  |
|-----------------------|-----------------------------------------|
| No retention policy   | Files accumulate forever                |
| No cleanup automation | Manual deletion when remembered         |
| `_archive/` is manual | Move files by hand to exclude from sync |
| No per-game pinning   | Can't say "quals26 uses this exact set" |

### Access Control Issues

| Issue              | Impact                                             |
|--------------------|----------------------------------------------------|
| Shared password    | `blackteam/blackteam` for all authenticated access |
| No machine tokens  | CI/CD must use same creds or AWS directly          |
| No token lifecycle | Can't revoke access after game handover            |

---

## Requirements

### Must Have

1. **Content-addressed storage** - Immutable versions identified by content hash
2. **Mutable tags** - `latest`, `release`, game-specific tags point to versions
3. **Direct push** - Upload to artifacts server without going through S3
4. **Tag-based retention** - Tagged versions kept, untagged versions expire
5. **Tag flushing** - Remove a tag globally, triggering garbage collection
6. **SSO for humans** - Authentik integration for browser access
7. **Bearer tokens for machines** - Revocable tokens for CI/CD and ops container

### Should Have

1. **Per-game manifests** - Snapshot of exact versions used for a competition
2. **Storage tiering** - Use both disks effectively (e.g., tagged vs untagged)
3. **S3 backup for durable artifacts** - Tagged/stable versions replicated to S3
4. **Observability** - Structured logging, Sentry integration, OpenTelemetry support
   - Covers: FastAPI service, Caddy, server-side tools (magpie-ctl gc, sync)

### Future Work (Out of Scope)

1. **OpenNebula auto-refresh** - Manual/Terraform for now
2. **Granular token scopes** - Per-team, per-service tokens
3. **Token enable/disable** - Without regenerating

---

## Proposed Design

### Architecture Overview

**Key principle:** Caddy serves all downloads directly from the filesystem. A Python service
(codename: **magpie**) handles uploads and tag management only. State is encoded in the filesystem
structure, not a database.

```text
                                    +---------------------+
                                    |     Caddy           |
                                    | (file server, TLS)  |
                                    +----------+----------+
                                               |
                    +--------------------------+---------------------------+
                    |                          |                           |
                    v                          v                           v
        GET /artifacts/...         GET /api/v1/...            POST /api/v1/upload/...
        (static file serving)      (reverse proxy)            (reverse proxy)
                    |                          |                           |
                    v                          |                           |
        +-------------------+                  |                           |
        |  /data/artifacts/ |                  |                           |
        |  (filesystem)     |                  v                           v
        +-------------------+         +---------------------------------------+
                                      |     Python Service (uvicorn)         |
                                      |  - Upload handling                   |
                                      |  - Tag management (symlinks)         |
                                      |  - Metadata (sidecar JSON)           |
                                      |  - Token validation                  |
                                      +---------------------------------------+
```

### Technology Stack

| Component    | Choice        | Rationale                                     |
|--------------|---------------|-----------------------------------------------|
| File Server  | Caddy         | Already used, direct TLS, fast static serving |
| Upload/API   | FastAPI       | Async file handling, OpenAPI docs             |
| State        | Filesystem    | Symlinks for tags, JSON sidecars for metadata |
| CLI (client) | Click         | Mature, well-tested                           |
| CLI Name     | `magpie`      | Bird-themed naming convention                 |
| Server Admin | `magpie-ctl`  | Server-side operations (init, sync, gc)       |
| Database     | SQLite        | Token storage only; filesystem is main state  |
| Logging      | structlog     | Structured JSON logging, OTEL correlation     |
| Error/APM    | Sentry SDK    | Error tracking + performance monitoring       |
| Telemetry    | OpenTelemetry | Vendor-neutral traces and metrics             |

### Tool Separation

| Tool         | Runs where        | Purpose                                   |
|--------------|-------------------|-------------------------------------------|
| `magpie`     | Client (anywhere) | Push, get, tag, ls - talks to API         |
| `magpie-ctl` | Server only       | Init, sync, gc - direct filesystem access |
| uvicorn      | Server            | Runs the FastAPI service                  |

### Filesystem Structure

Extension is part of the artifact path. Each artifact is a directory containing versioned blobs
organized into subdirectories:

```text
/data/artifacts/
+-- images/
|   +-- sources/
|   |   +-- windows-server-2019.iso/       # Artifact directory
|   |       +-- .magpie                    # Manifest (JSON, contains tags)
|   |       +-- blobs/                     # Content-addressed blob storage
|   |       |   +-- abc12345               # Blob file (8-char hash, no @ prefix)
|   |       +-- metadata/                  # Metadata sidecars
|   |       |   +-- abc12345.json          # Metadata for blob abc12345
|   |       +-- latest -> blobs/abc12345   # Symlink (derived from .magpie)
|   |
|   +-- infra/
|   |   +-- vrouter.qcow2/                 # Artifact directory
|   |       +-- .magpie                    # {"version":1,"tags":{"latest":"@def67890"}}
|   |       +-- blobs/
|   |       |   +-- abc12345               # Older version blob
|   |       |   +-- def67890               # Current version blob
|   |       +-- metadata/
|   |       |   +-- abc12345.json
|   |       |   +-- def67890.json
|   |       +-- latest -> blobs/def67890   # Symlink (derived from .magpie)
|   |       +-- quals26 -> blobs/abc12345  # Symlink (derived from .magpie)
|   |
|   +-- games/2026/quals/
|       +-- ad-server.qcow2/
|           +-- .magpie
|           +-- blobs/
|           |   +-- 111aaabb
|           +-- metadata/
|           |   +-- 111aaabb.json
|           +-- latest -> blobs/111aaabb
|           +-- release -> blobs/111aaabb
|
+-- assets/2026/quals/...                  # Non-image files (same pattern)
|
+-- public/                                # Unauthenticated
    +-- swccdc-root.crt
```

**Key decisions:**

- Extension is part of the artifact path (e.g., `vrouter.qcow2/` is the artifact directory)
- Blobs stored in `blobs/` subdirectory without `@` prefix for cleaner filesystem
- Metadata stored in `metadata/` subdirectory for separation of concerns
- `.magpie` manifest uses `@hash` format for tag values (e.g., `"latest": "@def67890"`)
- Symlinks point to `blobs/{hash}` (relative path)
- `.magpie` is the source of truth for tags (JSON manifest)
- Symlinks are derived state from `.magpie` - Caddy follows them transparently
- GC reconciles symlinks against `.magpie` (self-healing)

### `.magpie` Manifest Format

Each artifact directory contains a `.magpie` file:

```json
{
  "version": 1,
  "tags": {
    "latest": "@def67890",
    "quals26": "@abc12345"
  }
}
```

**Fields:**

- `version` - Schema version (currently 1), for future compatibility
- `tags` - Map of tag name to hash reference (uses `@` prefix in manifest)

**Invariants:**

- `.magpie` is the source of truth for tags
- Symlinks are derived from `.magpie` (created/updated by tag operations, reconciled by GC)
- Tag operations update `.magpie` atomically, then update symlinks
- If symlinks drift from `.magpie`, GC fixes them

### Metadata Sidecar Format

Each blob has a JSON sidecar with metadata stored in the `metadata/` subdirectory:

`metadata/abc12345.json`:

```json
{
  "hash": "abc12345def67890...",
  "uploaded_by": "ci-bot",
  "uploaded_at": "2026-01-08T20:00:00Z",
  "source_uri": "https://github.com/SouthwestCCDC/deployment/tree/abc123/packer/vrouter/"
}
```

**Fields:**

- `hash` - Full SHA-256 (64 characters; filename only has first 8 chars)
- `uploaded_by` - Token name or username
- `uploaded_at` - ISO timestamp
- `source_uri` - Optional URL to source (GitHub repo path, CI job, vendor download)

### Authentication

**Two auth methods, unified through Caddy:**

1. **Humans (browser)** - Authentik forward auth (SSO)
2. **Machines (CI, ansible)** - Bearer tokens (magpie-issued, stored in SQLite)

**Caddy forward_auth flow:**

Caddy's `forward_auth` sends a GET request to the auth endpoint with the original request's
`Authorization` header preserved. Magpie validates and returns 200 (allow) or 401 (deny).

```Caddy
# Caddyfile (production)
forward_auth magpie:8000 {
    uri /api/v1/auth/validate
    copy_headers X-Magpie-User X-Magpie-Scope
}
```

The auth endpoint receives:

- `Authorization: Bearer mgp_xxx...` - Token to validate
- `X-Forwarded-Method: POST` - Original HTTP method
- `X-Forwarded-Uri: /api/v1/upload/...` - Original request path

On success, Caddy copies `X-Magpie-User` and `X-Magpie-Scope` headers to the upstream request
for logging and authorization decisions.

**Token storage:** SQLite with WAL mode (handles concurrency, single file)

**Token fields:**

- `name` - Human-readable identifier (e.g., "ci-bot")
- `token_hash` - SHA-256 of actual token (never store plaintext)
- `scope` - "read" | "write" | "admin"
- `enabled` - Toggle without deleting
- `created_at` - Timestamp

**Scope hierarchy:** `admin` > `write` > `read`

- `read` - download, list, info
- `write` - upload, tag/untag (implies read)
- `admin` - token management, GC, flush-tag (implies write)

**Token prefix:** `mgp_` for regular tokens, `mgp_ADMIN_` for break-glass token

**Token management:**

```bash
# Server-side (magpie-ctl)
magpie-ctl token create --name ci-bot --scope write
magpie-ctl token list
magpie-ctl token revoke ci-bot

# Via API (with admin token)
POST /api/v1/tokens
GET /api/v1/tokens
DELETE /api/v1/tokens/{name}
```

### Configuration

Configuration uses Pydantic Settings with a **file < env < CLI** hierarchy.

**Server settings** (environment variables with `MAGPIE_` prefix):

| Setting                 | Default                     | Description                                                   |
|-------------------------|-----------------------------|---------------------------------------------------------------|
| `MAGPIE_STORAGE_PATH`   | `/data/artifacts`           | Root path for artifact storage                                |
| `MAGPIE_TEMP_PATH`      | `{storage_path}/.tmp`       | Temp directory for uploads (same filesystem for atomic moves) |
| `MAGPIE_DATABASE_PATH`  | `{storage_path}/.magpie.db` | SQLite database for tokens                                    |
| `MAGPIE_RETENTION_DAYS` | `90`                        | Days to keep untagged blobs before GC                         |
| `MAGPIE_GC_LOCK_PATH`   | `/var/run/magpie-gc.lock`   | Lockfile for GC                                               |
| `MAGPIE_S3_BUCKET`      | *(none)*                    | S3 bucket for backup (optional)                               |
| `MAGPIE_DEBUG`          | `false`                     | Enable debug logging                                          |
| `MAGPIE_SENTRY_DSN`     | *(none)*                    | Sentry DSN for error/performance tracking                     |
| `MAGPIE_OTEL_ENDPOINT`  | *(none)*                    | OpenTelemetry collector endpoint (gRPC)                       |
| `MAGPIE_LOG_FORMAT`     | `json`                      | Log format: `json` or `console`                               |

**CLI configuration** (`~/.magpie/config.toml`):

```toml
[default]
server = "https://artifacts.swccdc.com"
token = "mgp_xxx..."
```

Environment variables override file settings: `MAGPIE_SERVER`, `MAGPIE_TOKEN`.

**Note:** `MAGPIE_TIMEOUT` is intentionally not read from the config file. It can only be
set via CLI flag (`--timeout`) or environment variable. This prevents long-lived global
configuration from silently affecting network behavior.

### Observability

Observability covers three pillars: **structured logging**, **tracing**, and **metrics**. All server-side
components (FastAPI service, Caddy, magpie-ctl tools) participate.

#### Structured Logging

All components emit structured JSON logs to stdout for consumption by log aggregators (Loki, etc.).

**FastAPI service + magpie-ctl:**

- Use `structlog` for structured logging
- JSON format by default (`MAGPIE_LOG_FORMAT=json`), with `console` option for local dev
- Standard fields: `timestamp`, `level`, `logger`, `message`, `request_id` (when applicable)
- Correlation: `trace_id` and `span_id` included when OTEL is enabled

**Caddy:**

- JSON access logs via Caddy's structured logging
- Log to stdout (captured by container runtime)
- Fields: timestamp, request method/path, status, duration, client IP, user agent

Example log output (FastAPI):

```json
{
  "timestamp": "2026-01-09T15:30:00Z",
  "level": "info",
  "logger": "magpie.api.upload",
  "message": "Upload complete",
  "request_id": "req_abc123",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "artifact": "images/infra/vrouter.qcow2",
  "hash": "@def67890",
  "size_bytes": 1073741824,
  "duration_ms": 4523
}
```

#### Sentry Integration

Sentry provides error tracking and performance monitoring (APM).

**Coverage:**

| Component  | Errors | Performance Tracing |
|------------|--------|---------------------|
| FastAPI    | Yes    | Yes (requests, DB)  |
| magpie-ctl | Yes    | Yes (GC, sync ops)  |
| Caddy      | No     | No (use OTEL)       |

**Implementation:**

- `sentry-sdk[fastapi]` for the FastAPI service
- `sentry-sdk` for CLI tools (magpie-ctl)
- DSN configured via `MAGPIE_SENTRY_DSN` environment variable
- Performance sampling rate configurable (default: 100% for this low-traffic service)
- Release tracking via `MAGPIE_VERSION` or git commit

**What gets captured:**

- Unhandled exceptions with full stack traces
- Request performance spans (FastAPI middleware)
- Database query spans (SQLite token lookups)
- Custom spans for long operations (GC walk, S3 sync)
- Breadcrumbs for debugging context

#### OpenTelemetry Support

OpenTelemetry (OTEL) provides vendor-neutral instrumentation for traces and metrics.

**Architecture:**

```text
+-----------------+    +-----------------+    +-----------------+
|  FastAPI        |    |  Caddy          |    |  magpie-ctl     |
|  (OTEL SDK)     |    |  (OTEL module)  |    |  (OTEL SDK)     |
+--------+--------+    +--------+--------+    +--------+--------+
         |                      |                      |
         +----------------------+----------------------+
                                |
                                v
                    +-----------------------+
                    |  OTEL Collector       |
                    |  (optional, or direct |
                    |   to backend)         |
                    +-----------------------+
                                |
                    +-----------+-----------+
                    v                       v
            +-------------+         +-------------+
            |  Traces     |         |  Metrics    |
            |  (Jaeger,   |         |  (Prometheus|
            |   Tempo)    |         |   etc.)     |
            +-------------+         +-------------+
```

**Implementation:**

- `opentelemetry-instrumentation-fastapi` for automatic request tracing
- `opentelemetry-instrumentation-httpx` for outbound HTTP (S3, etc.)
- Caddy OTEL module for access tracing (correlates with upstream spans)
- OTLP exporter to collector endpoint (`MAGPIE_OTEL_ENDPOINT`)
- Disabled by default (no-op when endpoint not configured)

**Trace context propagation:**

- W3C Trace Context headers (`traceparent`, `tracestate`)
- Caddy extracts/injects trace context for request correlation
- Enables end-to-end traces: Caddy -> FastAPI -> S3

#### Metrics

Custom application metrics exported via OpenTelemetry:

| Metric                           | Type      | Description                                   |
|----------------------------------|-----------|-----------------------------------------------|
| `magpie_uploads_total`           | Counter   | Total uploads (labels: artifact_path, status) |
| `magpie_upload_bytes_total`      | Counter   | Total bytes uploaded                          |
| `magpie_upload_duration_seconds` | Histogram | Upload duration                               |
| `magpie_gc_runs_total`           | Counter   | GC executions (labels: status)                |
| `magpie_gc_blobs_deleted`        | Counter   | Blobs removed by GC                           |
| `magpie_gc_bytes_freed`          | Counter   | Bytes freed by GC                             |
| `magpie_artifacts_total`         | Gauge     | Current artifact count                        |
| `magpie_storage_bytes`           | Gauge     | Current storage usage                         |

Caddy provides its own metrics (request count, duration, status codes) via its Prometheus endpoint
or OTEL export.

#### Configuration Summary

| Setting                | Purpose                                                   |
|------------------------|-----------------------------------------------------------|
| `MAGPIE_SENTRY_DSN`    | Enable Sentry (errors + APM)                              |
| `MAGPIE_OTEL_ENDPOINT` | Enable OTEL export (traces + metrics)                     |
| `MAGPIE_LOG_FORMAT`    | `json` (default) or `console`                             |
| `SENTRY_RELEASE`       | Version tag for Sentry releases (optional, auto-detected) |

All observability features are **opt-in**: if DSN/endpoint not set, that integration is disabled
with zero overhead (no-op exporters).

### Upload Behavior

**Duplicate detection:** When pushing a file whose SHA-256 matches an existing blob:

1. No new blob is written (content already exists)
2. `latest` tag is updated to point to the existing blob (unless `--no-latest`)
3. Metadata sidecar is **not** updated (preserves original upload info)
4. CLI emits: `Blob @abc12345 already exists. Use 'magpie amend' to update metadata.`

This makes pushes idempotent while preserving provenance.

### CLI Interface (`magpie`)

```bash
# Configure CLI
magpie config --server https://artifacts2.swccdc.com --token <token>

# Push - uploads file, auto-creates latest
magpie push vrouter.qcow2 --to images/infra/vrouter.qcow2
magpie push vrouter.qcow2 --to images/infra/vrouter.qcow2 --source-uri https://github.com/...
magpie push vrouter.qcow2 --to images/infra/vrouter.qcow2 --no-latest

# Download artifact (verifies SHA-256 by default)
magpie get images/infra/vrouter.qcow2/latest
magpie get images/infra/vrouter.qcow2/latest -o /tmp/router.qcow2
magpie get images/infra/vrouter.qcow2/latest --no-verify

# Get download URL (for use with curl/wget)
magpie url images/infra/vrouter.qcow2/latest

# List artifacts
magpie ls images/infra/vrouter.qcow2

# Get detailed info
magpie info images/infra/vrouter.qcow2/@abc12345

# Tag management
magpie tag images/infra/vrouter.qcow2 @abc12345 --as release
magpie untag images/infra/vrouter.qcow2 release
magpie flush-tag quals2023 --confirm-walk-filesystem

# Amend metadata
magpie amend images/infra/vrouter.qcow2/@abc12345 --source-uri https://github.com/...

# Garbage collection (triggers batch job on server)
magpie gc --dry-run
magpie gc
```

### Server Admin Tool (`magpie-ctl`)

```bash
# Initialize server
magpie-ctl init

# Reset break-glass token
magpie-ctl init --reset-admin-token

# Garbage collection (direct, for systemd timer)
magpie-ctl gc
magpie-ctl gc --dry-run
magpie-ctl gc --reconcile-only

# S3 sync
magpie-ctl sync --to-s3
magpie-ctl sync --from-s3
```

### URL Patterns

```text
# Specific version via blob path (immutable)
https://artifacts.swccdc.com/artifacts/images/infra/vrouter.qcow2/blobs/abc12345

# Follow a tag via symlink (mutable)
https://artifacts.swccdc.com/artifacts/images/infra/vrouter.qcow2/latest
```

**Note:** Tags resolve to blobs via symlinks. Caddy follows symlinks transparently, so accessing
`/artifacts/{path}/latest` returns the blob that `latest` points to.

### Garbage Collection

**GC performs two functions:**

1. **Blob cleanup** - Remove untagged blobs older than retention period (default 90 days)
2. **Symlink reconciliation** - Ensure symlinks match `.magpie` source of truth

**Invocation:**

| Method      | Command           | How it works                        |
|-------------|-------------------|-------------------------------------|
| Server-side | `magpie-ctl gc`   | Runs directly (for systemd timer)   |
| Via API     | `POST /api/v1/gc` | Spawns `magpie-ctl gc` as batch job |
| Client CLI  | `magpie gc`       | Calls API endpoint                  |

**Locking:** GC uses a lockfile (`/var/run/magpie-gc.lock`) to prevent concurrent runs.

### S3 Backup

**Model:** Single S3 bucket mirrors local filesystem structure for tagged artifacts only.

| Direction   | Command                     | When                    | What                              |
|-------------|-----------------------------|-------------------------|-----------------------------------|
| Local -> S3 | `magpie-ctl sync --to-s3`   | Nightly (systemd timer) | Tagged blobs + metadata + .magpie |
| S3 -> Local | `magpie-ctl sync --from-s3` | Bootstrap/restore       | Everything, recreate symlinks     |

**S3 structure mirrors local** (no symlinks - tags stored in `.magpie`):

```text
s3://magpie-artifacts/images/infra/vrouter.qcow2/
+-- .magpie
+-- blobs/
|   +-- abc12345
|   +-- def67890
+-- metadata/
    +-- abc12345.json
    +-- def67890.json
```

**S3 credentials:** IAM user with static access key, scoped to bucket only.

**Bootstrap flow:**

```bash
magpie-ctl init                  # Initialize empty server
magpie-ctl sync --from-s3        # Download from S3, recreate symlinks
```

---

## Resolved Design Questions

The following questions from the initial proposal have been resolved:

| Question                  | Resolution                                                   |
|---------------------------|--------------------------------------------------------------|
| Storage tier strategy     | Single pool for now; tiering deferred                        |
| Manifests vs tags         | Tags in `.magpie` file; manifests deferred                   |
| Implementation approach   | Python 3.13+ (FastAPI) service behind Caddy                  |
| S3 relationship           | Backup only; nightly sync of tagged artifacts                |
| CLI naming                | `magpie` (client), `magpie-ctl` (server admin)               |
| Tag storage               | `.magpie` JSON manifest per artifact directory               |
| Symlink management        | Derived from `.magpie`; GC reconciles drift                  |
| Token storage             | SQLite with WAL mode                                         |
| GC implementation         | Batch job via `magpie-ctl gc`; lockfile prevents concurrency |
| Repository name           | `SouthwestCCDC/magpie`                                       |
| Hash prefix length        | 8 characters (32 bits) - sufficient for this scale           |
| Temp file location        | Same filesystem as storage (configurable `MAGPIE_TEMP_PATH`) |
| Duplicate upload behavior | Return existing hash, update `latest`, warn about `--amend`  |
| Configuration approach    | Pydantic Settings; hierarchy: file < env < CLI arg           |
| CLI config location       | `~/.magpie/config.toml`                                      |
| Token management          | Both `magpie-ctl token` CLI and REST API                     |
| Forward auth endpoint     | `GET /api/v1/auth/validate`; returns 200/401                 |
| GC retention              | Server default + CLI override (`--retention-days`)           |
| GC lock path              | Configurable via `MAGPIE_GC_LOCK_PATH`                       |
| S3 credentials            | Standard AWS env vars / credential chain                     |
| S3 bucket config          | `MAGPIE_S3_BUCKET` setting                                   |
| Package manager           | uv (fast, modern, already used in ops container)             |
| Linting/formatting        | ruff (lint + format), bandit, aligned with scoring/ repo     |
| Observability stack       | structlog + Sentry + OpenTelemetry; all opt-in via env vars  |
| Log format                | JSON to stdout (default); console format for local dev       |
| OTEL export               | OTLP to configurable collector endpoint                      |
| Storage layout            | Subdirectories (`blobs/`, `metadata/`) for clean separation  |

## Open Questions

### 1. Locking Feature

The original proposal mentioned `artifacts lock games/2026/quals` to prevent further uploads.
This is deferred - implement if needed based on operational experience.

### 2. Per-Category Retention

Original proposal had different retention rules per category. Current design simplifies to:
tagged artifacts kept, untagged expire after 90 days (configurable). Revisit if needed.

### 3. Authentik JWT Support

Day one uses magpie-issued bearer tokens. Future work could validate Authentik JWTs directly
for unified identity. Design allows this addition later.

---

## Future Work

Items deferred for future implementation:

1. **S3 backup integration** - `magpie-ctl sync --to-s3` and `--from-s3` commands for backing up tagged artifacts
2. **OpenNebula integration** - Auto-refresh when `latest` changes
3. **Granular token scopes** - Per-team, per-service permissions
4. **Token enable/disable** - Toggle without regenerating
5. **Web UI** - Beyond Caddy file browser
6. **Notifications** - Webhook on new uploads
7. **Checksums** - Automatic `.sha256` generation and validation

---

## References

### Current Implementation

- [Artifacts role](https://github.com/SouthwestCCDC/infra-deployment/tree/main/ansible/roles/artifacts)
- [Bootstrap host vars](https://github.com/SouthwestCCDC/infra-deployment/blob/main/ansible/host_vars/10.3.3.100.yml)
- [AWS policies](https://github.com/SouthwestCCDC/infra-deployment/tree/main/terraform/aws/policies)
- [ONE images using artifacts](https://github.com/SouthwestCCDC/deployment/blob/master/terraform/prod/opennebula_platform/images.tf)

### Consumers

- [VyOS Packer build](https://github.com/SouthwestCCDC/deployment/tree/master/packer/base-images/vyos/equuleus)
- [Game asset downloads][game-assets] (example)
- [Windows root cert install][win-root-cert]

### Related Documentation

- [User Guide](user-guide.md)

[game-assets]: https://github.com/SouthwestCCDC/deployment/blob/master/ansible/roles/2025/regionals/cdn/tasks/main.yml
[win-root-cert]: https://github.com/SouthwestCCDC/deployment/blob/master/ansible/roles/2025/regionals/windows_laptop/tasks/main.yml
