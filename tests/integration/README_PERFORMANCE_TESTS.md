# Upload Performance Tests

This directory contains integration tests for upload performance and streaming behavior.

## Purpose

These tests verify that:
1. Uploads stream correctly without excessive buffering
2. Upload throughput remains stable throughout the upload
3. Large file uploads complete successfully
4. Memory usage remains bounded during uploads

## Test Tiers

### Always Run (PRs)

- **1 MB uploads** - Basic streaming verification (~0.1s)
- **50 MB uploads** - Medium file handling (~0.2s)
- **Throughput stability** - Detects degradation patterns (~0.1s)

**Total time: ~1.5 seconds**

These run in CI on every PR by default to provide fast feedback.

### Large Tests (Post-Merge/Manual)

- **500 MB uploads** - Buffer exhaustion detection (~5 seconds)

Large tests are skipped in PRs to keep CI fast. They run automatically after merge to `default` branch.

Enable manually with:

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

### PR Builds

Small and medium tests run on every PR (`.github/workflows/ci.yml`):
- Default: `MAGPIE_SKIP_LARGE_TESTS=1` (large tests skipped)
- Duration: ~1.5 seconds
- Fast feedback for streaming and basic performance verification

### Post-Merge Builds

Full test suite including large tests runs after merge to `default` (`.github/workflows/post-merge.yml`):
- Setting: `MAGPIE_SKIP_LARGE_TESTS=0` (all tests run)
- Duration: ~6 seconds total
- Comprehensive verification including 500MB upload test

### Timing Estimates

Based on local testing (development machine):

| Test | Size | Time | Throughput |
|------|------|------|------------|
| Small | 1 MB | 0.1s | N/A |
| Medium | 50 MB | 0.2s | N/A |
| Large | 500 MB | 5s | ~120 MB/s |

Actual CI times may vary based on runner performance.
