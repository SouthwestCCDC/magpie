# Upload Smoke Tests

This directory contains smoke tests for upload functionality.

## Purpose

These tests verify that uploads of various sizes complete successfully through the
FastAPI service and the full Caddy + FastAPI stack.

**What these tests verify:**
- Uploads return HTTP 200 and valid responses
- Various file sizes (1MB, 50MB) complete within reasonable timeouts
- Full stack integration (Caddy reverse proxy + FastAPI) functions correctly

**What these tests do NOT verify:**
- Server-side memory usage during uploads
- Whether the server processes uploads in a streaming fashion vs. buffering
- Actual throughput or performance characteristics beyond basic timing
- Server-side streaming behavior or buffer sizes

## Test Structure

### Integration Tests (`test_upload_smoke.py`)

Tests against FastAPI TestClient (no reverse proxy):

- **1 MB upload** - Basic upload completion
- **50 MB upload** - Medium file handling

### E2E Tests (`../e2e/test_upload_smoke.py`)

Tests through docker-compose stack (Caddy + FastAPI):

- **Basic uploads (1MB, 50MB)** - Verify full stack upload completion
- **Caddy latency test** - Measures chunk timing as indirect proxy buffering indicator

## Running Tests

### Integration tests (fast)

```bash
uv run pytest tests/integration/test_upload_smoke.py -v
```

### E2E tests (requires docker-compose)

```bash
# Start services
docker compose up -d

# Run tests
uv run pytest tests/e2e/test_upload_smoke.py -v

# Cleanup
docker compose down -v
```

### CI Behavior

All smoke tests run in CI on every PR:
- Integration tests: ~1-2 seconds
- E2E tests: ~5-10 seconds (includes docker-compose startup)

## What About Performance Testing?

Server-side performance validation requires instrumentation that these smoke tests
do not provide. For server-side streaming validation, see issue #TBD which tracks:

- `tracemalloc` or equivalent memory measurement during uploads
- Server-side logging of chunk/buffer state
- Realistic concurrent upload testing
- Caddy proxy buffering verification via logs

The E2E latency test provides some indirect evidence of buffering issues through
client-side timing measurements, but cannot definitively prove server-side streaming
behavior.

## Limitations

### Integration Tests

Integration tests use FastAPI's `TestClient`, which:
- Tests the API endpoint logic
- Does NOT test the Caddy reverse proxy
- Does NOT replicate production network conditions
- Cannot detect proxy buffering issues

### E2E Tests

E2E tests use `httpx.Client` against docker-compose services, which:
- Tests the full stack (Caddy + FastAPI)
- Can detect severe proxy configuration issues
- Still measures client-side observable behavior only
- Cannot directly measure server memory or streaming state

### What's Missing

To properly validate server-side streaming behavior and memory usage, we need:
- Server-side memory profiling (tracemalloc)
- Server-side logging of upload handler state
- Realistic concurrent upload testing
- Analysis of Caddy logs during large uploads
- Meaningful performance thresholds based on production requirements

These capabilities are tracked in a separate issue for future implementation.
