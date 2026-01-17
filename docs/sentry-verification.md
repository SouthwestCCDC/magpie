# Sentry Integration Verification Guide

This guide explains how to verify that Sentry error tracking is working correctly in Magpie.

## Prerequisites

1. A Sentry account and project (free tier works)
2. Running Magpie server instance
3. Access to server environment variables

## Setup

### 1. Get Your Sentry DSN

1. Log in to [sentry.io](https://sentry.io)
2. Navigate to your project settings
3. Go to "Client Keys (DSN)"
4. Copy the DSN (format: `https://<key>@<org>.ingest.sentry.io/<project>`)

### 2. Configure Magpie

Set the Sentry DSN environment variable:

```bash
export MAGPIE_SENTRY_DSN="https://your-key@your-org.ingest.sentry.io/your-project"
```

Or in docker-compose.yml:

```yaml
environment:
  - MAGPIE_SENTRY_DSN=https://your-key@your-org.ingest.sentry.io/your-project
```

### 3. Restart the Server

```bash
# If using docker-compose
docker-compose restart magpie-api

# If running directly
uvicorn magpie.server.app:app --reload
```

## Verification Tests

### Test 1: Verify Initialization

Check the server logs on startup. You should see Sentry initializing (if debug logging is enabled):

```bash
docker-compose logs magpie-api | grep -i sentry
```

### Test 2: Trigger a Test Error

Create a simple script to trigger an error:

```python
import httpx

# This should trigger a 404 (not a 500, so won't be captured)
response = httpx.get("http://localhost:8000/api/v1/info/nonexistent/path/@latest")
print(f"Status: {response.status_code}")

# To trigger a 500 error, you'd need to cause an actual server error
# For example, a malformed request that passes validation but fails processing
```

**Note:** Sentry's FastAPI integration only captures unhandled exceptions (5xx errors).
Expected client errors like 404 are not reported to Sentry by default.

### Test 3: Check Sentry Dashboard

1. Log in to your Sentry project
2. Navigate to Issues
3. Look for any errors from your Magpie instance
4. Errors should include:
   - Full stack traces
   - Request details (URL, method, headers)
   - Environment (development/production based on MAGPIE_DEBUG)
   - Server information

### Test 4: Verify Performance Monitoring

If you have performance monitoring enabled in Sentry:

1. Navigate to Performance in Sentry dashboard
2. Make some requests to your Magpie server:
   ```bash
   curl http://localhost:8000/health
   curl http://localhost:8000/api/v1/status
   ```
3. Check for transaction traces in Sentry showing:
   - Request duration
   - FastAPI route information
   - Any database queries (if applicable)

## What Gets Captured

### Automatically Captured

- **5xx Server Errors**: Unhandled exceptions in request handlers
- **FastAPI Exceptions**: Errors raised by FastAPI framework
- **Performance Traces**: Request timing and spans (sample rate: 100% debug, 10% production)
- **Request Context**: URL, method, headers, user info

### Not Captured (By Default)

- **4xx Client Errors**: 400, 401, 403, 404 are expected errors, not bugs
- **Handled Exceptions**: Exceptions caught and converted to error responses
- **PII**: Personally identifiable information (send_default_pii=False)

## Configuration Details

The Sentry integration is configured in `src/magpie/server/observability.py`:

```python
sentry_sdk.init(
    dsn=settings.sentry_dsn,
    integrations=[
        StarletteIntegration(),
        FastApiIntegration(),
    ],
    environment="development" if settings.debug else "production",
    traces_sample_rate=1.0 if settings.debug else 0.1,
    send_default_pii=False,
)
```

### Configuration Options

| Setting | Description | Default |
|---------|-------------|---------|
| `MAGPIE_SENTRY_DSN` | Sentry DSN (required to enable) | `None` |
| `MAGPIE_DEBUG` | Affects environment tag and sample rate | `false` |
| Environment tag | `development` when debug=True, else `production` | Based on DEBUG |
| Trace sampling | 100% in debug, 10% in production | Based on DEBUG |
| PII protection | Never sends personal data | `send_default_pii=False` |

## Troubleshooting

### Sentry Not Initializing

1. Check that `MAGPIE_SENTRY_DSN` is set:
   ```bash
   docker-compose exec magpie-api env | grep SENTRY
   ```

2. Verify DSN format is correct:
   ```
   https://<public_key>@<org>.ingest.sentry.io/<project_id>
   ```

3. Check for initialization errors in logs

### No Errors Appearing in Sentry

1. **Verify you're triggering 5xx errors**, not 4xx errors
2. Check Sentry project settings for rate limits
3. Verify network connectivity from server to sentry.io
4. Check if Sentry is in maintenance mode

### Performance Traces Not Showing

1. Verify your Sentry plan includes performance monitoring
2. Check trace sample rate settings
3. Make multiple requests to generate enough samples

## Automated Testing

The integration tests verify Sentry configuration without actually sending data:

```bash
# Run Sentry integration tests
pytest tests/integration/test_sentry_integration.py -v

# Run all observability tests
pytest tests/unit/test_observability.py -v
```

## Production Considerations

### Sample Rate

In production, only 10% of transactions are traced to reduce overhead:

```python
traces_sample_rate=0.1  # when debug=False
```

Adjust this in `observability.py` if you need different sampling.

### Environment Tags

The environment tag helps separate issues:
- `development` - when `MAGPIE_DEBUG=true`
- `production` - when `MAGPIE_DEBUG=false`

Set appropriate filters in Sentry dashboard to focus on production issues.

### Release Tracking

For better error tracking, set a release version:

```bash
export SENTRY_RELEASE=magpie@0.1.0
```

Or configure in the code (future enhancement).

## References

- [Sentry FastAPI Documentation](https://docs.sentry.io/platforms/python/integrations/fastapi/)
- [Magpie Design Doc - Observability](./design.md#sentry-integration)
- [Magpie Configuration](./user-guide.md#server-environment-variables)
