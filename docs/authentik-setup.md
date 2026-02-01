# Authentik SSO Integration Guide

Optional browser-based SSO for human access to artifacts. Bearer tokens remain the primary auth method for CLI, API, and automation.

**Architecture:** Browser → Authentik (login) → Caddy (validates session) → /artifacts/*

## Setup

1. Create Authentik Forward Auth Provider (type: "Proxy Provider")
   - External host: `https://magpie.example.com`
   - Token validity: `hours=24` (adjust as needed)

2. Create Authentik Application pointing to that provider
   - Launch URL: `https://magpie.example.com/artifacts/`

3. Note the forward auth endpoint: `https://authentik.example.com/outpost.goauthentik.io/auth/caddy`

4. Set environment variable:
```bash
AUTHENTIK_HOST=authentik.example.com
```

5. In the consolidated `Caddyfile`, replace Bearer auth in `/artifacts/*` handler with:
```Caddy
handle /artifacts/* {
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

6. Restart Caddy:
```bash
docker compose -f docker-compose.prod.yml restart caddy
```

## Verification

**API access** (Bearer tokens still work):
```bash
curl -H "Authorization: Bearer $MAGPIE_TOKEN" https://magpie.example.com/api/v1/artifacts
```

**Browser access** (without SSO): Should return 401
**Browser access** (with SSO): After login, shows directory listing
**CLI access**: Works regardless of SSO configuration

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Redirect loop | Verify External Host matches MAGPIE_DOMAIN, Caddy can reach Authentik |
| Invalid headers | Check forward_auth URL ends with `/auth/caddy` |
| Tokens broken | Test `/api/v1/auth/validate`, check Caddyfile route order |
| Access denied | Verify user groups and check Authentik logs |

## Security Notes

- Sessions managed by Authentik (duration set in provider)
- Client headers stripped before authentication (defense-in-depth)
- Requires network connectivity between Caddy and Authentik
- Bearer tokens remain the auth method for all API and CLI access

---

*(AI-generated via Claude Code w/ Sonnet 4.5)*
