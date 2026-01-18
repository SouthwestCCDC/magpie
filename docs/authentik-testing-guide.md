# Authentik SSO Integration - Testing Guide

This guide provides instructions for manually testing the Authentik SSO integration after deployment.

## Prerequisites

- Magpie deployed with `docker-compose.prod.yml`
- Authentik configured per [authentik-setup.md](authentik-setup.md)
- `AUTHENTIK_HOST` environment variable set (if enabling SSO)
- Bearer token for API testing (create via `magpie-ctl token create`)

## Test Plan Overview

| Test # | Description | Expected Result |
|--------|-------------|-----------------|
| 1 | Bearer token auth works | API requests succeed with valid token |
| 2 | Unauthenticated browser access | Redirect to Authentik login (if SSO enabled) |
| 3 | Authenticated browser access | Directory listing visible after login |
| 4 | Session persistence | No re-login after closing tab |
| 5 | API access unaffected | CLI commands work with bearer tokens |
| 6 | Invalid token rejection | 401 error for invalid tokens |

## Test 1: Bearer Token Authentication (API)

**Purpose**: Verify bearer token auth still works for API/CLI access.

### Setup
```bash
# Get an admin token
# Note: Container name may vary. Use 'docker compose ps' to find the correct name.
export MAGPIE_TOKEN=$(docker compose -f docker-compose.prod.yml exec -T magpie magpie-ctl token create --name test-token --scope admin | grep "Token:" | awk '{print $2}')
echo $MAGPIE_TOKEN
```

### Test Commands
```bash
# Test upload (requires write/admin scope)
curl -H "Authorization: Bearer $MAGPIE_TOKEN" \
  -F "file=@test.txt" \
  -F "source_uri=https://example.com/test" \
  https://magpie.example.com/api/v1/upload/test/example

# Expected: 201 Created with JSON response containing hash

# Test list artifacts (public endpoint, but token should still work)
curl -H "Authorization: Bearer $MAGPIE_TOKEN" \
  https://magpie.example.com/api/v1/artifacts

# Expected: 200 OK with JSON list of artifacts

# Test token list (admin-only endpoint)
curl -H "Authorization: Bearer $MAGPIE_TOKEN" \
  https://magpie.example.com/api/v1/tokens

# Expected: 200 OK with JSON list of tokens
```

### Validation
- All API requests return expected status codes
- No authentication errors
- Token headers are properly validated

## Test 2: Unauthenticated Browser Access

**Purpose**: Verify Authentik SSO redirects unauthenticated users.

**Note**: This test only applies if `AUTHENTIK_HOST` is configured and the forward_auth block is uncommented in Caddyfile.prod.

### Steps
1. Open an incognito/private browser window
2. Navigate to `https://magpie.example.com/artifacts/`
3. Observe behavior

### Expected Results

**With Authentik SSO enabled** (forward_auth uncommented):
- Browser redirects to Authentik login page
- URL changes to Authentik domain
- Login form is presented

**Without Authentik SSO** (forward_auth commented, default):
- Directory listing shown immediately
- No authentication required
- Public access maintained

## Test 3: Authenticated Browser Access

**Purpose**: Verify authenticated users can browse artifacts.

**Applies to**: Authentik SSO enabled deployments only.

### Steps
1. Continue from Test 2 (at Authentik login page)
2. Enter valid Authentik credentials
3. Submit login form
4. Observe redirect back to `/artifacts/`

### Expected Results
- Redirect to `https://magpie.example.com/artifacts/` after login
- Directory listing is visible
- Can navigate artifact folders
- Can download files by clicking links

### Verification
Check browser developer tools (F12) for:
- Response headers include `X-authentik-username`
- No 401 or 403 errors
- Session cookie set by Authentik

## Test 4: Session Persistence

**Purpose**: Verify Authentik sessions persist across browser tabs/windows.

**Applies to**: Authentik SSO enabled deployments only.

### Steps
1. Complete Test 3 (logged in and browsing artifacts)
2. Close the browser tab
3. Open a new tab in the same browser session
4. Navigate to `https://magpie.example.com/artifacts/`

### Expected Results
- No login prompt
- Directory listing shown immediately
- Session persists (Authentik remembers authentication)

### Notes
- Session duration configured in Authentik provider settings
- Default is typically 24 hours
- Closing the entire browser may clear session (depends on Authentik config)

## Test 5: CLI Access Unaffected

**Purpose**: Verify CLI tools work with bearer tokens regardless of Authentik SSO.

### Setup
```bash
# Configure CLI client
magpie config --server https://magpie.example.com --token $MAGPIE_TOKEN
```

### Test Commands
```bash
# Push an artifact
echo "test content" > test-file.txt
magpie push test-file.txt --to test/cli-test

# Expected: Upload succeeds, shows hash and tags

# Get artifact info
magpie info test/cli-test:latest

# Expected: Shows artifact metadata

# List artifacts
magpie ls test/cli-test

# Expected: Shows versions/tags

# Download artifact
magpie get test/cli-test:latest

# Expected: Downloads file to current directory
```

### Validation
- All CLI commands succeed
- No authentication errors
- Bearer token auth works independently of browser SSO

## Test 6: Invalid Token Rejection

**Purpose**: Verify invalid/missing tokens are rejected properly.

### Test Commands
```bash
# Test with no token
curl https://magpie.example.com/api/v1/upload/test/example \
  -F "file=@test.txt"

# Expected: 401 Unauthorized

# Test with invalid token
curl -H "Authorization: Bearer invalid_token_xyz" \
  https://magpie.example.com/api/v1/upload/test/example \
  -F "file=@test.txt"

# Expected: 401 Unauthorized

# Test with malformed header
curl -H "Authorization: invalid_token_xyz" \
  https://magpie.example.com/api/v1/upload/test/example \
  -F "file=@test.txt"

# Expected: 401 Unauthorized (missing "Bearer" prefix)
```

### Validation
- All requests return 401 Unauthorized
- Error messages are informative but not revealing internals
- No stack traces in production mode

## Test 7: Mixed Authentication (Advanced)

**Purpose**: Verify bearer tokens and Authentik SSO coexist properly.

**Applies to**: Authentik SSO enabled deployments only.

### Scenario 1: Browser with Token Header

```bash
# Make API request from browser DevTools console
fetch('https://magpie.example.com/api/v1/artifacts', {
  headers: {
    'Authorization': 'Bearer YOUR_TOKEN_HERE'
  }
})
.then(r => r.json())
.then(console.log)

# Expected: 200 OK, bearer token takes precedence
```

### Scenario 2: CLI Download of Browser-Accessible Artifact

```bash
# Upload via browser (authenticated with Authentik)
# Then download via CLI (authenticated with bearer token)

magpie get test/browser-upload:latest

# Expected: Download succeeds regardless of upload auth method
```

### Validation
- Bearer tokens work for API endpoints regardless of Authentik SSO
- Authentik SSO only affects browser access to `/artifacts/*`
- API endpoints (`/api/v1/*`) continue using bearer tokens exclusively

## Troubleshooting

### Issue: Redirect loop between Magpie and Authentik

**Symptoms**:
- Browser keeps redirecting
- Never reaches login page or artifacts

**Diagnosis**:
```bash
# Check Caddy logs (use 'docker compose ps' to find container name if needed)
docker compose -f docker-compose.prod.yml logs caddy | grep forward_auth

# Check Authentik provider configuration
# Verify External Host matches MAGPIE_DOMAIN exactly
```

**Solution**:
- Ensure `External Host` in Authentik provider = `MAGPIE_DOMAIN`
- Verify forward_auth URL is correct
- Check that Caddy can reach Authentik (network connectivity)

### Issue: "Invalid authentication headers"

**Symptoms**:
- Login succeeds in Authentik
- Redirect back to Magpie fails with auth error

**Diagnosis**:
```bash
# Check which headers Authentik is sending
docker compose -f docker-compose.prod.yml logs caddy | grep X-authentik

# Verify forward_auth block copies correct headers
grep "copy_headers" Caddyfile.prod
```

**Solution**:
- Verify forward_auth URL ends with `/auth/caddy`
- Check that `copy_headers` line includes Authentik headers
- Ensure Authentik provider type is "Forward auth (single application)"

### Issue: Bearer tokens stop working

**Symptoms**:
- CLI commands fail with 401
- API requests with valid tokens rejected

**Diagnosis**:
```bash
# Test auth validation endpoint directly
curl -H "Authorization: Bearer $MAGPIE_TOKEN" \
  https://magpie.example.com/api/v1/auth/validate

# Expected: 200 OK with X-Magpie-User and X-Magpie-Scope headers
```

**Solution**:
- Verify `/api/v1/auth/validate` is public (no forward_auth)
- Check that API endpoints use magpie's forward_auth, not Authentik's
- Review Caddyfile route order (more specific routes first)

### Issue: Public endpoints require authentication

**Symptoms**:
- Cannot list artifacts without authentication
- `/artifacts/*` requires login even when Authentik disabled

**Diagnosis**:
```bash
# Check if forward_auth block is commented
grep -A3 "handle /artifacts" Caddyfile.prod

# Verify AUTHENTIK_HOST is empty
docker compose -f docker-compose.prod.yml exec caddy env | grep AUTHENTIK
```

**Solution**:
- Ensure forward_auth block in `/artifacts/*` handler is commented
- Or ensure `AUTHENTIK_HOST` env var is not set
- Restart Caddy after configuration changes

## Automated Testing (Future Work)

For comprehensive automated testing, consider:

1. **Integration Tests**
   - Mock Authentik forward_auth endpoint
   - Test Caddy routing with different auth states
   - Verify header propagation

2. **E2E Tests**
   - Use Playwright/Selenium for browser automation
   - Test full login flow with Authentik test instance
   - Verify session persistence

3. **Security Tests**
   - Test token leakage scenarios
   - Verify HTTPS enforcement
   - Test CSRF protection (if applicable)

## Reporting Results

After completing tests, document results:

```markdown
## Test Results

| Test | Status | Notes |
|------|--------|-------|
| 1. Bearer token auth | ✅ Pass | All API calls succeeded |
| 2. Unauthenticated redirect | ✅ Pass | Redirected to Authentik |
| 3. Authenticated access | ✅ Pass | Directory listing visible |
| 4. Session persistence | ✅ Pass | No re-login required |
| 5. CLI access | ✅ Pass | All commands work |
| 6. Invalid token rejection | ✅ Pass | Proper 401 errors |
| 7. Mixed auth | ✅ Pass | Both methods coexist |

**Environment**:
- Magpie version: [version]
- Authentik version: [version]
- Caddy version: [version]
- AUTHENTIK_HOST: [set/unset]
```

Include any errors, unexpected behavior, or edge cases encountered.
