# Production Checklist

Before deploying to production:

## Pre-Deployment
- [ ] A reverse proxy is planned/provisioned for TLS termination in front
      of magpie -- the bundled image serves plain HTTP only. See
      [installation.md](installation.md#server-installation).
- [ ] Firewall allows the port your reverse proxy listens on (typically 80/443)
- [ ] Persistent storage provisioned for `MAGPIE_DATA_DIR`
- [ ] Backup destination configured (see [backup-restore.md](backup-restore.md))
- [ ] `MAGPIE_ADMIN_TOKEN_SINK` chosen (`file`/`exec`/`discard`/`stdout`) --
      the canonical `docker-compose.yml` refuses to start without it
      (fail-closed, no default). See
      [installation.md](installation.md#admin-token-delivery).

## Security Setup
- [ ] `MAGPIE_ADMIN_TOKEN_SINK` set to a non-`stdout` value (`file`, `exec`,
      or `discard`) -- `stdout` is opt-in only and reintroduces the
      container-log leak this checklist previously required guarding against
- [ ] Admin token delivered and stored securely via the configured sink (or
      intentionally discarded, with a mint-later plan)
- [ ] HTTPS enabled at your reverse proxy (magpie itself does not terminate TLS)
- [ ] `MAGPIE_TRUSTED_PROXIES` left unset unless the bundled Caddy sits behind
      another reverse proxy you control -- if it does (the standard
      production setup), set it to that proxy's exact address(es), never a
      broad range. A broad range (e.g. RFC1918) lets any client on it spoof
      `X-Forwarded-For` and bypass `MAGPIE_ALLOWED_CIDRS`. See
      [installation.md](installation.md#trusted-proxies).
- [ ] If relying on `MAGPIE_ALLOWED_CIDRS` for token-less reads,
      `MAGPIE_TRUSTED_PROXIES` is actually set to your proxy's hop --
      `magpie-deploy.sh` gates on this combination (prompts on `install`,
      hard-fails `update` with `--noninteractive`) if it isn't. See
      [installation.md](installation.md#trusted-proxies).
- [ ] If upgrading an existing install that used built-in TLS termination
      (`--tls-mode auto`/`manual`) to v0.2.0+, a reverse proxy is in place
      and `update --accept-builtin-tls-removed` has been acknowledged. See
      [installation.md](installation.md#upgrading-to-v020).

## Deploy
```bash
base=https://github.com/SouthwestCCDC/magpie/releases/latest/download
curl -fsSLO "$base/docker-compose.yml" -fsSLO "$base/env.example" -fsSLO "$base/SHA256SUMS"
grep -E ' (docker-compose\.yml|env\.example)$' SHA256SUMS | sha256sum -c
cp env.example .env
echo 'MAGPIE_ADMIN_TOKEN_SINK=file' >> .env
# Set MAGPIE_DATA_DIR, MAGPIE_UID/GID, MAGPIE_BIND_IP, and
# MAGPIE_TRUSTED_PROXIES in .env as appropriate for your host.
docker compose up -d
```

Each release's `docker-compose.yml` pins its bundled image by digest. Keep it with the matching
`env.example` and `SHA256SUMS`; see [Release Assets](releases.md#release-assets). The installer
is an optional alternative for hosts where you want systemd and account management; see
[Installation](installation.md#server-installation).

Retrieve the admin token per the chosen sink -- see
[installation.md](installation.md#admin-token-delivery) for the `file`,
`exec`, and `discard` (mint-later) flows.

## Validation
- [ ] Health check: `curl https://magpie.example.com/health` (through your reverse proxy)
- [ ] Upload test: `magpie push test.txt --to test/artifact`
- [ ] Download test: `magpie get test/artifact:latest`
- [ ] HTTPS enforced at the reverse proxy (HTTP either redirects or is not exposed externally)

## Operations
- [ ] Monitoring configured (setup `/health` endpoint checks)
- [ ] Backup script scheduled (see [backup-restore.md](backup-restore.md))
- [ ] GC schedule planned
- [ ] Token rotation procedure documented
- [ ] Admin token recovery procedure tested

**Recover admin token if lost:**
```bash
docker compose -f docker-compose.yml exec magpie magpie-ctl init --reset-admin-token
```
The new token is delivered through the same configured `MAGPIE_ADMIN_TOKEN_SINK`
(not printed to stdout unless `sink=stdout`) -- delivery is attempted before
any database change, so if it fails (e.g. a broken exec command), the prior
admin token is left completely valid and unchanged; fix the sink and re-run.
If using `sink=discard`, mint a fresh admin-scope token directly instead:
```bash
docker compose -f docker-compose.yml exec magpie \
  magpie-ctl token create --name ops-admin --scope admin
```

---

*(AI-generated via Claude Code w/ Sonnet 4.5; updated for the v0.2.0 bundled-image topology via Claude Code w/ Opus 4.8; refreshed for v0.2.0 final via Devin; latest-release URLs updated via Devin)*
