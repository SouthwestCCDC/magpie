# TokenService Caching Strategy

## Overview

The TokenService is implemented as a cached singleton using Python's `@functools.lru_cache` decorator to optimize performance in the FastAPI web server context.

## Problem Statement (Issue #426)

In a FastAPI application, dependency injection functions like `get_token_service()` are called on every request that requires authentication. Without caching, this would create a new TokenService instance for each request, leading to:

1. Repeated database initialization overhead (`init_database()` on every request)
2. Multiple database connections being created and torn down
3. WAL mode pragma execution on every connection
4. Table schema validation via `CREATE TABLE IF NOT EXISTS`

While `init_database()` is idempotent and safe to call multiple times, the overhead is significant when called thousands of times per second under load.

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
| Iterations/sec | ~33M | ~3.2K | - |
| `init_database()` calls | 1 (total) | 1 per request | - |

Run `pytest tests/unit/test_token_service_performance.py -v -s` to measure performance on your environment.

The caching eliminates 99.99% of the overhead associated with TokenService instantiation.

## Thread Safety

### lru_cache Thread Safety

Python's `functools.lru_cache` is thread-safe by design:
- Uses an internal lock to protect the cache dictionary
- Multiple threads can safely call `get_token_service()` concurrently
- Only one TokenService instance will be created, even under concurrent access

### TokenService Thread Safety

The TokenService itself is designed for safe concurrent use:

1. **Immutable after construction**: Once created, the TokenService configuration never changes
2. **No shared connection state**: Each method creates its own SQLite connection via `get_connection()`
3. **WAL mode concurrency**: SQLite's Write-Ahead Logging mode allows concurrent readers
4. **Connection-scoped transactions**: Each operation uses its own connection and transaction

Example:
```python
import hashlib

def validate_token(self, token: str) -> TokenInfo | None:
    # Hash the token for lookup
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    
    # Create new connection per call (not shared between requests)
    conn = get_connection(self.db_path)
    try:
        stored_token = get_token_by_hash(conn, token_hash)
    finally:
        conn.close()  # Clean up connection
```

### Known Limitations

- **Settings changes**: If `MagpieSettings` changes after the first `get_token_service()` call, the cached instance will continue using the old settings
- **Workaround**: Call `clear_token_service_cache()` to force recreation (useful in tests)
- **Production impact**: None - production settings are configured once at startup and never change

## Testing

### Unit Tests

`tests/unit/test_deps.py` verifies the caching behavior:
- Same instance returned on multiple calls
- Cache clearing creates new instance

### Benchmark Tests

`tests/unit/test_token_service_performance.py` measures performance:
- Verifies `init_database()` is called only once with caching
- Quantifies speedup compared to uncached access
- Ensures cached access is at least 10x faster (typically ~9000x)

### Integration Tests

Integration tests use `clear_token_service_cache()` in fixtures to ensure fresh instances when needed (see `tests/integration/conftest.py`).

## Alternatives Considered

### Option 1: Module-level singleton with Lock

```python
_token_service: TokenService | None = None
_lock = threading.Lock()

def get_token_service() -> TokenService:
    global _token_service
    if _token_service is None:
        with _lock:
            if _token_service is None:
                _token_service = TokenService(get_settings())
    return _token_service
```

**Rejected**: More complex, harder to test, no advantage over `lru_cache`.

### Option 2: Accept current behavior

**Rejected**: Benchmark showed unacceptable overhead (316µs per request just for TokenService instantiation).

### Option 3: Lazy database initialization with module-level flag

**Rejected**: Would require global state and thread synchronization, defeating the purpose of using dependency injection.

## References

- Issue: [#426](https://github.com/SouthwestCCDC/magpie/issues/426)
- Original PR: [#422](https://github.com/SouthwestCCDC/magpie/pull/422)
- Python docs: [`functools.lru_cache`](https://docs.python.org/3/library/functools.html#functools.lru_cache)
- SQLite docs: [Write-Ahead Logging](https://www.sqlite.org/wal.html)
