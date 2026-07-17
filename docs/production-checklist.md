# Production Checklist

Before deploying to production:

## Pre-Deployment
- [ ] DNS configured for `MAGPIE_DOMAIN`
- [ ] Firewall allows ports 80 and 443
- [ ] Persistent storage provisioned for `MAGPIE_DATA_DIR`
- [ ] Backup destination configured (see [backup-restore.md](backup-restore.md))
- [ ] `MAGPIE_ADMIN_TOKEN_SINK` chosen (`file`/`exec`/`discard`/`stdout`) --
      `docker-compose.prod.yml` refuses to start without it (fail-closed, no
      default). See [installation.md](installation.md#admin-token-delivery).

## Security Setup
- [ ] `MAGPIE_DEBUG=false`
- [ ] `MAGPIE_ADMIN_TOKEN_SINK` set to a non-`stdout` value (`file`, `exec`,
      or `discard`) -- `stdout` is opt-in only and reintroduces the
      container-log leak this checklist previously required guarding against
- [ ] Admin token delivered and stored securely via the configured sink (or
      intentionally discarded, with a mint-later plan)
- [ ] HTTPS enabled (automatic via Let's Encrypt)

## Deploy
```bash
export MAGPIE_DOMAIN=magpie.example.com
export MAGPIE_DATA_DIR=/path/to/persistent/storage
export MAGPIE_ADMIN_TOKEN_SINK=file   # or exec / discard / stdout
docker compose -f docker-compose.prod.yml up -d
```

Retrieve the admin token per the chosen sink -- see
[installation.md](installation.md#admin-token-delivery) for the `file`,
`exec`, and `discard` (mint-later) flows.

## Validation
- [ ] Health check: `curl https://$MAGPIE_DOMAIN/health`
- [ ] Upload test: `magpie push test.txt --to test/artifact`
- [ ] Download test: `magpie get test/artifact:latest`
- [ ] HTTPS redirect works: `curl http://$MAGPIE_DOMAIN` (redirects to https)

## Operations
- [ ] Monitoring configured (setup `/health` endpoint checks)
- [ ] Backup script scheduled (see [backup-restore.md](backup-restore.md))
- [ ] GC schedule planned
- [ ] Token rotation procedure documented
- [ ] Admin token recovery procedure tested

**Recover admin token if lost:**
```bash
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl init --reset-admin-token
```
The new token is delivered through the same configured `MAGPIE_ADMIN_TOKEN_SINK`
(not printed to stdout unless `sink=stdout`) -- delivery is attempted before
any database change, so if it fails (e.g. a broken exec command), the prior
admin token is left completely valid and unchanged; fix the sink and re-run.
If using `sink=discard`, mint a fresh admin-scope token directly instead:
```bash
docker compose -f docker-compose.prod.yml exec magpie \
  magpie-ctl token create --name ops-admin --scope admin
```

---

*(AI-generated via Claude Code w/ Sonnet 4.5)*
