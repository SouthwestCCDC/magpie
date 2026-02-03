# Upload Performance Tests

This directory contains integration tests for upload performance and streaming behavior.

## Purpose

These tests verify that:
1. Uploads stream correctly without excessive buffering
2. Upload throughput remains stable throughout the upload
3. Large file uploads complete successfully
4. Memory usage remains bounded during uploads

## Test Tiers

### Always Run (CI)

- **1 MB uploads** - Basic streaming verification
- **50 MB uploads** - Medium file handling
- **Throughput stability** - Detects degradation patterns

These run in CI and local development by default.

### Large Tests (Manual/Nightly)

- **500 MB uploads** - Buffer exhaustion detection

Large tests are skipped by default to keep CI fast. Enable them with:

```bash
MAGPIE_SKIP_LARGE_TESTS=0 uv run pytest tests/integration/test_upload_performance.py -v
```

Or run just the large tests:

```bash
MAGPIE_SKIP_LARGE_TESTS=0 uv run pytest tests/integration/test_upload_performance.py -m slow -v
```

## What These Tests Catch

### Buffering Issues

If the API or storage layer buffers the entire file in memory before processing:
- Read operations will be single large blocks instead of streaming
- Tests will fail with assertion: "File was read in a single operation"

### Throughput Degradation

If upload speed degrades significantly during transfer (e.g., starts at 40 MB/s, drops to 1-2 MB/s):
- Throughput stability tests will fail
- Indicates buffering/backpressure issues

### Timeout Failures

If large uploads fail near completion:
- Large file tests will timeout or fail
- Indicates buffer exhaustion or resource issues

## Running Specific Test Sizes

```bash
# Only 1 MB tests
uv run pytest tests/integration/test_upload_performance.py -k "1-10" -v

# Only 50 MB tests
uv run pytest tests/integration/test_upload_performance.py -k "50-60" -v

# All except large (default)
uv run pytest tests/integration/test_upload_performance.py -m "not slow" -v
```

## Related Tests

For full-stack testing through Caddy proxy, see:
- `tests/e2e/test_upload_performance.py` - E2E tests with docker-compose

## CI Configuration

In CI, large tests are skipped automatically (default `MAGPIE_SKIP_LARGE_TESTS=1`).

For nightly jobs, set `MAGPIE_SKIP_LARGE_TESTS=0` to enable comprehensive testing.
