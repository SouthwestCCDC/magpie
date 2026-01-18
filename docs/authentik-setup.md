# Authentik SSO Integration Guide

This guide explains how to configure Authentik SSO for human browser access to Magpie artifacts.

## Overview

Magpie supports two authentication methods:

1. **Bearer tokens** - For machine access (CI/CD, scripts, CLI tools)
2. **Authentik SSO** - For human browser access to `/artifacts/*` paths

This allows team members to browse artifacts via web browser using their SSO credentials, while automation continues to use API tokens.

## Architecture

```
Browser → Caddy → Authentik (forward_auth) → Caddy → /artifacts/*
                     ↓
              (validates session)
                     ↓
              (returns user headers)
```

When a human visits `/artifacts/*`:
1. Caddy intercepts the request and sends it to Authentik for validation
2. If no valid session exists, Authentik redirects to its login page
3. After successful login, Authentik validates the session and returns user identity headers
4. Caddy forwards the request with user headers to the file server
5. User can browse artifacts with directory listing

## Prerequisites

- Running Authentik instance (v2023.8 or later recommended)
- Admin access to Authentik to create applications and providers
- Magpie deployed with Caddyfile.prod configuration
- DNS configured for both Magpie and Authentik domains

## Authentik Configuration

### Step 1: Create a Forward Auth Provider

1. Log in to Authentik admin interface
2. Navigate to **Applications** → **Providers**
3. Click **Create** and select **Proxy Provider**
4. Configure the provider:

   | Field | Value | Notes |
   |-------|-------|-------|
   | **Name** | `Magpie Artifacts` | Descriptive name |
   | **Authorization flow** | `default-provider-authorization-implicit-consent` | Or your preferred flow |
   | **Type** | `Forward auth (single application)` | Critical setting |
   | **External host** | `https://magpie.example.com` | Your Magpie domain |
   | **Token validity** | `hours=24` | Adjust as needed |

5. Click **Create**

### Step 2: Create an Application

1. Navigate to **Applications** → **Applications**
2. Click **Create**
3. Configure the application:

   | Field | Value |
   |-------|-------|
   | **Name** | `Magpie Artifacts` |
   | **Slug** | `magpie-artifacts` |
   | **Provider** | Select the provider created in Step 1 |
   | **Launch URL** | `https://magpie.example.com/artifacts/` |

4. Click **Create**

### Step 3: Configure Access Control (Optional)

1. Navigate to **Applications** → **Applications** → **Magpie Artifacts**
2. Click the **Policy / Group / User Bindings** tab
3. Add policies to control who can access artifacts:
   - Bind specific groups (e.g., `swccdc-team`)
   - Bind specific users
   - Create custom policies based on attributes

### Step 4: Get the Authentik Endpoint

The forward auth endpoint URL format for Authentik (as of v2023.8+) is:
```
https://authentik.example.com/outpost.goauthentik.io/auth/caddy
```

Replace `authentik.example.com` with your Authentik domain.

**Note**: This path structure is specific to Authentik's Caddy integration. Verify the exact path in your Authentik version's documentation if using a different version. The path may vary in older versions or custom outpost configurations.

## Magpie Configuration

### Environment Variables

Add this environment variable to your Magpie deployment:

```bash
# Authentik SSO configuration - just the hostname, not the full URL
AUTHENTIK_HOST=authentik.example.com
```

### Docker Compose Configuration

Update your `docker-compose.prod.yml` or deployment configuration:

```yaml
services:
  caddy:
    environment:
      - MAGPIE_DOMAIN=magpie.example.com
      - AUTHENTIK_HOST=authentik.example.com
```

### Enable Forward Auth in Caddyfile

The forward_auth block is commented out by default in `Caddyfile.prod`. To enable Authentik SSO:

1. Locate the `/artifacts/*` handler section in `Caddyfile.prod`
2. Comment out the default handler (without SSO)
3. Uncomment the Authentik SSO handler block:

```Caddy
handle /artifacts/* {
    # Strip client-provided auth headers (defense-in-depth)
    request_header -X-authentik-username
    request_header -X-authentik-email
    request_header -X-authentik-name
    request_header -X-authentik-groups

    forward_auth {$AUTHENTIK_HOST} {
        uri /outpost.goauthentik.io/auth/caddy
        copy_headers X-authentik-username X-authentik-email X-authentik-name X-authentik-groups
    }
    root * /data
    file_server browse
}
```

4. Restart the Caddy container (run from the directory containing docker-compose.prod.yml):

```bash
docker compose -f docker-compose.prod.yml restart caddy
```

**Note:** The `request_header -X-authentik-*` directives strip any client-provided headers before authentication. This prevents malicious clients from spoofing identity headers.

### Alternative: Manual Caddyfile Configuration

If you're not using environment variables, you can hardcode the Authentik hostname in `Caddyfile.prod`:

1. Locate the `/artifacts/*` handler section
2. Comment out the default handler and uncomment the SSO handler:

```Caddy
handle /artifacts/* {
    # Strip client-provided auth headers (defense-in-depth)
    request_header -X-authentik-username
    request_header -X-authentik-email
    request_header -X-authentik-name
    request_header -X-authentik-groups

    forward_auth authentik.example.com {
        uri /outpost.goauthentik.io/auth/caddy
        copy_headers X-authentik-username X-authentik-email X-authentik-name X-authentik-groups
    }
    root * /data
    file_server browse
}
```

## Testing the Integration

### Test 1: Unauthenticated Access

1. Open an incognito/private browser window
2. Navigate to `https://magpie.example.com/artifacts/`
3. You should be redirected to Authentik login page
4. **Expected**: Redirect to Authentik login

### Test 2: Authenticated Access

1. Log in with your Authentik credentials
2. You should be redirected back to `/artifacts/`
3. Directory listing should be visible
4. **Expected**: See artifact directory structure

### Test 3: Bearer Token Access Still Works

Machine access via bearer tokens should remain functional:

```bash
# Upload with bearer token (should work)
curl -H "Authorization: Bearer mgp_xxx" \
  -F "file=@test.tar.gz" \
  https://magpie.example.com/api/v1/upload/images/test

# Download via API with token (should work)
magpie get images/test:latest
```

### Test 4: Session Persistence

1. Browse artifacts in your browser
2. Close the browser tab
3. Reopen `https://magpie.example.com/artifacts/`
4. **Expected**: No login prompt (session persists)

## Troubleshooting

### Issue: Redirect loop between Magpie and Authentik

**Cause**: Authentik forward auth endpoint is being protected by forward_auth itself.

**Solution**: Ensure the Authentik callback URLs are excluded from authentication:
- `/outpost.goauthentik.io/*` paths should not require forward_auth
- Check that External Host in Authentik provider matches your Magpie domain exactly

### Issue: "Invalid authentication headers" error

**Cause**: Authentik is not sending expected headers.

**Solution**:
1. Verify the forward auth URL is correct (should end with `/auth/caddy`)
2. Check Authentik provider type is "Forward auth (single application)"
3. Verify Caddy can reach the Authentik endpoint (network connectivity)

### Issue: Bearer tokens stop working

**Cause**: forward_auth applied to API endpoints incorrectly.

**Solution**:
- forward_auth for Authentik should ONLY be on `/artifacts/*`
- API endpoints (`/api/v1/*`) should continue using magpie's forward_auth
- Check Caddyfile order - more specific handlers should come first

### Issue: "Access denied" even for authorized users

**Cause**: Authentik policy binding is too restrictive.

**Solution**:
1. Check Application bindings in Authentik
2. Verify user is in allowed groups
3. Test with a superuser account to rule out policy issues
4. Check Authentik logs for policy evaluation details

## Security Considerations

### Session Management

- Authentik manages session cookies (not Magpie)
- Session duration configured in Authentik provider settings
- Sessions can be revoked centrally in Authentik

### Header Validation

The following headers are passed from Authentik to Magpie:
- `X-authentik-username` - Authentik username
- `X-authentik-email` - User's email address
- `X-authentik-name` - User's display name
- `X-authentik-groups` - Comma-separated group list (optional)

These headers are added by Authentik and trusted by Caddy. They can be logged for audit purposes.

### Network Security

- Authentik forward auth endpoint should be accessible from Caddy server
- If Authentik and Magpie are on different networks, use private networking or VPN
- TLS required for production (enabled by default in Caddyfile.prod)

## Hybrid Authentication Summary

After configuration, Magpie supports:

| Access Method | Authentication | Use Case |
|---------------|----------------|----------|
| Web browser → `/artifacts/*` | Authentik SSO | Humans browsing artifacts |
| CLI/API → `/api/v1/*` | Bearer tokens | CI/CD, scripts, automation |
| Direct download | Public or Token | Depending on endpoint |

## Advanced Configuration

### Custom User Attributes

If you need custom attributes from Authentik (e.g., team membership), configure additional headers in the Caddy forward_auth block:

```Caddy
forward_auth {$AUTHENTIK_HOST} {
    uri /outpost.goauthentik.io/auth/caddy
    copy_headers X-authentik-username X-authentik-email X-authentik-name X-authentik-groups X-authentik-uid
}
```

Remember to also add corresponding `request_header -X-authentik-<attr>` directives to strip those headers from incoming requests.

### Logging User Access

To log which users access artifacts, enable Caddy JSON access logs and filter for `X-authentik-username` header:

```json
{
  "ts": 1234567890,
  "request": {
    "uri": "/artifacts/images/ubuntu/latest",
    "headers": {
      "X-Authentik-Username": ["george"]
    }
  }
}
```

### Per-Path Authorization

For fine-grained access control (e.g., restrict certain artifact paths), implement authorization logic in Authentik policies or use a reverse proxy middleware layer.

## Migration Path

### Phase 1: Deploy Without SSO (Default)

Deploy with the forward_auth block commented (default state). Artifacts remain publicly browsable (existing behavior).

### Phase 2: Enable Authentik SSO

1. Set `AUTHENTIK_HOST` environment variable
2. Comment out the default handler and uncomment the Authentik SSO handler in Caddyfile.prod
3. Restart Caddy container
4. Test with manual testing guide

Humans must use SSO to browse. CI/CD continues with bearer tokens.

### Phase 3: Monitor and Adjust

- Review Caddy access logs for authentication patterns
- Adjust Authentik session duration if needed
- Update policies based on team feedback

### Rollback

To disable SSO, comment out the forward_auth block and restart Caddy.

## References

- [Authentik Forward Auth Documentation](https://goauthentik.io/docs/providers/proxy/forward_auth)
- [Caddy forward_auth Directive](https://caddyserver.com/docs/caddyfile/directives/forward_auth)
- Magpie Design Doc: `docs/design.md` (line 370 for auth architecture)
- Magpie Issue #176: Authentik SSO integration specification

---
*This documentation was generated with AI assistance (Claude Code w/ Opus 4.5).*
