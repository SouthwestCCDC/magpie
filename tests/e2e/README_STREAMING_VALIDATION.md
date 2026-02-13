# Server-Side Upload Streaming Validation

This directory contains E2E tests that verify server-side upload streaming behavior
using tracemalloc instrumentation.

## Purpose

These tests address the gaps identified in issue #438 and the adversarial review of PR #439:

**What smoke tests don't catch:**
- Server-side buffering issues (double-buffering, memory bloat)
- Memory usage during uploads
- Performance degradation from inefficient I/O patterns

**What streaming validation tests verify:**
- Peak memory delta during uploads (proves streaming vs buffering)
- Concurrent upload memory usage (detects resource leaks)
- Upload throughput (detects double-buffering)

## Tests

### test_large_upload_memory_bounded

Uploads a 500MB file and verifies the server's peak memory delta stays under 50MB.

**Memory threshold breakdown:**
- Temporary buffer pages: 8-64KB chunks
- Parser state and hasher: ~50KB
- FastAPI/uvicorn overhead: ~1-2MB
- SQLite metadata writes: ~1MB
- GC overhead margin: ~5-10MB
- **Total threshold: 50MB**

A non-streaming implementation would show 500MB+ memory delta.

### test_concurrent_uploads_memory_bounded

Runs 3 concurrent 100MB uploads and verifies peak memory stays under 100MB total.

**Why this matters:**
- Multiple buffered uploads would multiply memory usage (300MB+)
- Resource leaks only appear under concurrent load
- Tests backpressure handling (slow client scenarios)

### test_throughput_not_degraded

Uploads 100MB and verifies throughput exceeds 250 MB/s (2x degradation margin).

**Baseline expectations:**
- Local Docker network: 500+ MB/s
- Acceptable threshold: 250 MB/s (2x margin for CI variance)

Throughput below 250 MB/s indicates:
- Excessive memory pressure causing swapping
- Double-buffering through multiple layers
- Synchronous I/O blocking the upload stream

## Running Tests

### Full E2E Suite

```bash
uv run pytest tests/e2e/test_upload_streaming_validation.py -v
```

The docker-compose stack is managed automatically by pytest fixtures.

### Individual Test

```bash
uv run pytest tests/e2e/test_upload_streaming_validation.py::TestServerSideStreamingValidation::test_large_upload_memory_bounded -v
```

## Implementation Details

### Server-Side Instrumentation

Test-only endpoints at `/api/v1/_test/memory/*`:
- `POST /api/v1/_test/memory/reset` - Take baseline memory snapshot, start tracemalloc
- `GET /api/v1/_test/memory/stats` - Get peak/current memory delta since reset
- `POST /api/v1/_test/memory/stop` - Stop tracemalloc and clear baseline state

**Security:**
- Requires admin scope
- Only enabled when `MAGPIE_ENABLE_TEST_ENDPOINTS=true`
- Default: disabled
- Never enable in production

### Test Methodology

1. **Reset memory tracking:** Establish baseline before upload
2. **Upload via file stream:** Use real file I/O (tempfile), not in-memory buffers
3. **Get memory stats:** Measure peak delta from baseline
4. **Assert memory bounded:** Verify threshold not exceeded

### Why Real File I/O

Tests use `tempfile.NamedTemporaryFile` and read via file handles rather than
in-memory byte buffers. This ensures:
- Realistic I/O patterns (disk reads, OS buffering)
- Client-side streaming (not httpx buffering entire file)
- Accurate simulation of production uploads

## Relationship to Smoke Tests

| Test Type | Location | Verifies | Detects |
|-----------|----------|----------|---------|
| **Smoke tests** | `tests/e2e/test_upload_smoke.py` | Upload completion | Severe failures |
| **Streaming validation** | `tests/e2e/test_upload_streaming_validation.py` | Server-side behavior | Buffering issues |

Both are needed:
- Smoke tests catch broken uploads (fast, basic coverage)
- Streaming validation catches performance regressions (slower, deep coverage)

## Caddy Validation

The issue also mentions Caddy proxy buffering validation. These tests implicitly
verify Caddy behavior because:
- E2E tests go through full stack (Caddy + FastAPI)
- Any misconfiguration that causes Caddy to buffer full request bodies would show up
  as increased memory usage or degraded throughput in these tests

Validation that the Caddy configuration disables unwanted buffering (for example,
via appropriate `request_body`/`reverse_proxy` settings) is handled separately in
infrastructure and deployment documentation, not in these E2E tests.

## CI Behavior

These tests run in CI but may be slower than smoke tests:
- 500MB upload test: ~1-5 seconds
- 3x100MB concurrent: ~1-3 seconds
- 100MB throughput: ~0.5-1 second

Total: ~3-10 seconds including docker-compose startup.

---

*This documentation was generated with AI assistance (Claude Code w/ Opus 4.6).*
