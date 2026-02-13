# TokenService Caching Strategy

## Overview

The TokenService is implemented as a cached singleton using Python's `@functools.lru_cache` decorator to optimize performance in the FastAPI web server context.

## Problem Statement (Issue #426)

In a FastAPI application, dependency injection functions like `get_token_service()` are called on every request that requires authentication. Without caching, this creates a new TokenService instance for each request, causing repeated database initialization overhead (`init_database()`, connection setup, WAL mode pragma, table validation). While idempotent, this overhead is significant under load.

## Solution

### Implementation

```python
@functools.lru_cache(maxsize=1)
def get_token_service() -> TokenService:
    """Get TokenService instance (cached singleton)."""
    settings = get_settings()
    return TokenService(settings)
```

The `@lru_cache(maxsize=1)` decorator caches the first TokenService instance and returns it for all subsequent calls, as long as the arguments remain the same.

### Performance Impact

Benchmark results (example from development environment; exact numbers will vary by machine, OS, and filesystem):

| Metric | Cached | Uncached | Speedup |
|--------|--------|----------|---------|
| Time per call | 0.03µs | 316µs | 9065x |
| `init_database()` calls | 1 (total) | 1 per request | - |

Run `pytest tests/unit/test_token_service_performance.py -v -s` to measure performance on your environment.

The caching eliminates 99.99% of the overhead associated with TokenService instantiation.

## Thread Safety

The implementation is thread-safe:
- `@lru_cache` uses an internal lock to protect the cache dictionary
- Multiple threads can safely call `get_token_service()` concurrently
- Only one TokenService instance will be created, even under concurrent access
- TokenService instances are immutable after construction
- Each method creates its own SQLite connection (no shared connection state)
- SQLite WAL mode allows concurrent readers

### Known Limitations

- **Settings changes**: If `MagpieSettings` changes after the first `get_token_service()` call, the cached instance will continue using the old settings
- **Workaround**: Call `clear_token_service_cache()` to force recreation (useful in tests)
- **Production impact**: None - production settings are configured once at startup and never change

## Testing

Tests verify caching behavior (same instance returned, cache clearing works) and measure performance. The benchmark tests confirm that `init_database()` is called only once with caching and quantify the speedup (~9000x typically). See `tests/unit/test_deps.py` and `tests/unit/test_token_service_performance.py`.

## References

- Issue: [#426](https://github.com/SouthwestCCDC/magpie/issues/426)
- Original PR: [#422](https://github.com/SouthwestCCDC/magpie/pull/422)
- Python docs: [`functools.lru_cache`](https://docs.python.org/3/library/functools.html#functools.lru_cache)
- SQLite docs: [Write-Ahead Logging](https://www.sqlite.org/wal.html)
