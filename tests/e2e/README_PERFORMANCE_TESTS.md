# E2E Upload Performance Tests

End-to-end tests for upload performance through the full deployment stack (Caddy + FastAPI + Storage).

## Purpose

These tests verify upload behavior through the complete deployment stack, catching issues that integration tests might miss:

1. **Proxy buffering** - Caddy reverse proxy configuration
2. **Full-stack throughput** - Realistic deployment performance
3. **Stack integration** - Interactions between all layers
4. **Production-like behavior** - docker-compose deployment

## Difference from Integration Tests

| Aspect | Integration Tests | E2E Tests |
|--------|------------------|-----------|
| Stack | FastAPI only | Caddy + FastAPI + Storage |
| Speed | Fast (< 1s) | Slower (starts docker-compose) |
| Catches | API/storage issues | Proxy buffering, full-stack issues |
| Runs | Always in CI | Via `-m e2e` flag |

## Running E2E Performance Tests

```bash
# All E2E performance tests (requires docker-compose)
uv run pytest tests/e2e/test_upload_performance.py -v

# Small/medium tests only (skip large)
uv run pytest tests/e2e/test_upload_performance.py -m "e2e and not slow" -v

# Include large (500 MB) tests
MAGPIE_SKIP_LARGE_TESTS=0 uv run pytest tests/e2e/test_upload_performance.py -v
```

## Test Coverage

### Full Stack Upload Tests

- **1 MB** - Basic proxy + API streaming
- **50 MB** - Medium file through full stack
- **500 MB** - Large file (skipped by default)

Each test verifies:
- Upload completes successfully
- Throughput is acceptable
- File is read in chunks (streaming, not buffering)

### Stability Tests

**Caddy No-Buffering Test** - Verifies Caddy doesn't buffer uploads by checking that throughput doesn't degrade significantly between start and end of upload.

**Memory Bounded Test** - Verifies concurrent uploads don't cause unbounded memory growth.

## What These Tests Catch

### Proxy Buffering

Caddy's `request_body` directive can cause buffering if misconfigured:

```caddyfile
# BAD - buffers entire request
reverse_proxy magpie:8000

# GOOD - streams request body
reverse_proxy magpie:8000 {
    request_body {
        max_size 0  # Unlimited streaming
    }
}
```

E2E tests will fail if proxy buffers uploads.

### Throughput Patterns

If uploads start fast (40 MB/s) but degrade (1-2 MB/s):
- Stability tests will fail
- Indicates buffering at some layer

### Stack Integration Issues

- Timeouts between Caddy and FastAPI
- Backpressure handling
- Connection reuse issues

## Docker Compose Setup

These tests use the main `docker-compose.yml` with temporary data directories for isolation.

The fixture `e2e_services` provides:
- `base_url`: http://localhost:8080 (through Caddy)
- `admin_token`: For authenticated requests

## Debugging Failures

If E2E tests fail but integration tests pass:

1. **Check Caddy configuration** - Look for buffering directives
2. **Check docker-compose logs** - Look for timeout/resource errors
3. **Check container resources** - Memory/CPU limits
4. **Run integration tests** - Isolate FastAPI from proxy

## CI Configuration

E2E performance tests run in CI with:
- `pytest -m e2e` flag
- Default skipping of large (500 MB) tests
- Docker-compose service startup

For nightly jobs, enable large tests with `MAGPIE_SKIP_LARGE_TESTS=0`.

## Related Tests

For direct API testing (no proxy), see:
- `tests/integration/test_upload_performance.py` - Integration performance tests
