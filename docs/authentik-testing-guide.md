# Authentik SSO - Testing Guide

Manual testing procedures for Authentik SSO integration after deployment.

## Prerequisites

- Magpie deployed with `docker-compose.prod.yml`
- Authentik configured per [authentik-setup.md](authentik-setup.md)
- `AUTHENTIK_HOST` environment variable set
- Bearer token for API testing

## Test 1: Bearer Token Authentication

```bash
# Create test token
export MAGPIE_TOKEN=$(docker compose -f docker-compose.prod.yml exec -T magpie \
  magpie-ctl token create --name test-token --scope admin | grep "Token:" | awk '{print $2}')

# Test upload
curl -H "Authorization: Bearer $MAGPIE_TOKEN" \
  -F "file=@test.txt" \
  https://magpie.example.com/api/v1/upload/test/example

# Expected: 201 Created

# Test list artifacts
curl -H "Authorization: Bearer $MAGPIE_TOKEN" \
  https://magpie.example.com/api/v1/artifacts

# Expected: 200 OK
```

## Test 2: Browser Access (Unauthenticated)

Open incognito browser to `https://magpie.example.com/artifacts/`.

- **Without Authentik SSO** (default): Returns 401 Unauthorized (Bearer token authentication required by default)
- **With Authentik SSO enabled**: Redirects to Authentik login page

## Test 3: Browser Access (Authenticated)

Log in with Authentik credentials. Should redirect to `/artifacts/` and show directory listing. Check browser DevTools for `X-authentik-username` header.

## Test 4: Session Persistence

After logging in, close and reopen the browser tab. Should show directory listing without re-login (session persists).

## Test 5: CLI Access Unaffected

```bash
magpie config --server https://magpie.example.com --token $MAGPIE_TOKEN

echo "test" > test-file.txt
magpie push test-file.txt --to test/cli-test
magpie info test/cli-test:latest
magpie ls test/cli-test
magpie get test/cli-test:latest
```

All CLI commands should work regardless of Authentik SSO configuration.

## Test 6: Invalid Token Rejection

```bash
# No token
curl https://magpie.example.com/api/v1/upload/test/example -F "file=@test.txt"
# Expected: 401 Unauthorized

# Invalid token
curl -H "Authorization: Bearer invalid" \
  https://magpie.example.com/api/v1/upload/test/example -F "file=@test.txt"
# Expected: 401 Unauthorized
```

## Troubleshooting

**Redirect loop**: Check Caddy logs. Verify External Host in Authentik provider = MAGPIE_DOMAIN. Confirm Caddy can reach Authentik.

**Invalid headers**: Verify forward_auth URL ends with `/auth/caddy`. Check provider type is "Forward auth (single application)".

**Bearer tokens broken**: Test `/api/v1/auth/validate`. Ensure API endpoints use Magpie's forward_auth, not Authentik's. Check Caddyfile route order.

**Artifacts require auth**: This is expected in production: `/artifacts/*` is protected by Caddy `forward_auth` using Bearer tokens by default. If access fails unexpectedly, verify you are sending a valid token (e.g., test it with `/api/v1/auth/validate`) and that Caddy forwards the `Authorization` header correctly. Do not comment out the `forward_auth` block on `/artifacts/*` in production, as that would make artifacts publicly accessible.
