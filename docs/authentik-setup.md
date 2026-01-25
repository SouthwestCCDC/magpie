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

### Step 3: Get the Authentik Endpoint

The forward auth endpoint URL format for Authentik (v2023.8+):

```
https://authentik.example.com/outpost.goauthentik.io/auth/caddy
```

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
2. In Caddyfile.prod, replace the current Bearer token forward_auth block with the
   Authentik forward_auth block (commented out by default) and uncomment the
   `request_header` directives that strip client-provided auth headers
3. Restart the Caddy container:

Example configuration:

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

Run from the directory containing docker-compose.prod.yml:

```bash
docker compose -f docker-compose.prod.yml restart caddy
```

**Note:** The `request_header -X-authentik-*` directives strip client-provided headers before authentication.


## Testing

**Before enabling Authentik SSO**: By default, `/artifacts/*` is protected by Bearer token authentication. Unauthenticated browser access should result in 401 Unauthorized.

**After enabling Authentik SSO**: Open incognito browser to `https://magpie.example.com/artifacts/` - should redirect to Authentik login. After logging in with Authentik credentials, should show directory listing.

**Bearer tokens still work**: API access via bearer tokens is unaffected by Authentik SSO.

## Troubleshooting

**Redirect loop**: Verify External Host in Authentik provider matches MAGPIE_DOMAIN. Ensure forward_auth endpoint is reachable.

**Invalid headers**: Check forward auth URL ends with `/auth/caddy`. Verify provider type is "Forward auth (single application)".

**Bearer tokens broken**: Ensure Authentik forward_auth is only on `/artifacts/*`, not `/api/v1/*`. Check Caddyfile route order.

**Access denied**: Verify user is in allowed groups. Test with admin account. Check Authentik logs.

## Security

- **Sessions**: Authentik manages cookies; duration configured in provider settings. Sessions can be revoked centrally.
- **Headers**: Authentik passes `X-authentik-username`, `X-authentik-email`, `X-authentik-name`, `X-authentik-groups`. Client-provided headers are stripped before authentication.
- **Network**: Authentik endpoint must be reachable from Caddy. Use private networking or VPN if on different networks. TLS required for production.

## Authentication Methods

By default, `/artifacts/*` uses Bearer token authentication. Authentik SSO is an optional replacement for human browser access:

| Method | Endpoint | Use Case | Default? |
| --- | --- | --- | --- |
| Bearer tokens | `/artifacts/*`, `/api/v1/*` | All programmatic access (CLI, API, automation) | Yes (production default) |
| Authentik SSO | `/artifacts/*` | Human browser access (replaces Bearer auth when enabled) | No (optional) |
| Public (no auth) | `/artifacts/public/*` | Public artifacts (no token required) | Yes |

## See Also

- [Authentik Testing Guide](authentik-testing-guide.md) - Manual testing procedures
- [Design Doc](design.md) - Auth architecture
