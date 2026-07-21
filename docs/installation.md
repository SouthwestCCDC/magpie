# Installation Guide

## Quick Start (Development)

```bash
git clone https://github.com/SouthwestCCDC/magpie
cd magpie
docker compose up --build
```

Server runs at `http://localhost:8080` (change via `MAGPIE_HTTP_PORT`).

## Production Deployment

```bash
export MAGPIE_DATA_DIR=/srv/magpie/data
export MAGPIE_ADMIN_TOKEN_SINK=file   # required -- see Admin Token Delivery below
export MAGPIE_IMAGE=ghcr.io/southwestccdc/magpie:X.Y.Z-bundled   # see note below
docker compose -f docker-compose.yml up -d
```

`MAGPIE_IMAGE` must be set explicitly to a published `<version>-bundled`
tag (check [the release list](https://github.com/SouthwestCCDC/magpie/releases)
for the latest) -- without it, `docker-compose.yml` falls back to
`ghcr.io/southwestccdc/magpie:latest`, which currently still points at a
different, incompatible image (built from the plain `Dockerfile`, serving
on container port 8000, not this compose file's 8080). This manual path
is for advanced/non-systemd deployments only; **use
[`scripts/magpie-deploy.sh`](../scripts/magpie-deploy.sh)** (see the
[Quick Start Guide](quickstart.md)) for a normal bare-metal/VM install --
it resolves and pins the correct `-bundled` tag automatically.

`-f docker-compose.yml` pins the canonical operator file explicitly -- a
bare `docker compose up` from a repo checkout auto-merges
`docker-compose.override.yml` (the local-development overlay: builds from
source, defaults the admin-token sink, enables test endpoints), which is
correct for [Quick Start (Development)](#quick-start-development) above
but must never run in production. Every `docker compose` command on this
page pins it the same way.

Magpie serves plain HTTP only on `MAGPIE_HTTP_PORT` (default 8080) -- it
does not terminate TLS itself. Put a reverse proxy (nginx, another Caddy, a
cloud load balancer, infra-deployment's outer Caddy, etc.) in front of it
for HTTPS. This is also what
[`scripts/magpie-deploy.sh`](../scripts/magpie-deploy.sh) (the recommended
installer for a bare-metal/VM host) deploys -- see the
[Quick Start Guide](quickstart.md).

## Admin Token Delivery

On first boot (no existing database), magpie generates a break-glass admin
token and delivers it via the sink configured by `MAGPIE_ADMIN_TOKEN_SINK`.
It is **never** printed to stdout (and therefore never captured by
`docker logs` or a log shipper) unless `sink=stdout` is explicitly chosen.

The canonical `docker-compose.yml` requires this variable to be set
explicitly -- there is no Compose-level default, by design (fail-closed;
an unset value means the container exits non-zero at boot rather than
running with no admin token delivered). `docker-compose.override.yml`
(the local-development overlay, merged in automatically by a bare `docker
compose up` inside a repo checkout) defaults it to `sink=file` for
frictionless local bootstrap -- that default does not apply to a real
deployment. See [.env.example](../.env.example) for the full list of
`MAGPIE_ADMIN_TOKEN_SINK*` variables.

Choose one:

- **`file`** -- writes a root-only (0600) file at
  `MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH` (default: `/data/admin-token`, set by
  `docker-compose.yml`):
  ```bash
  export MAGPIE_ADMIN_TOKEN_SINK=file
  docker compose -f docker-compose.yml up -d
  sudo cat "${MAGPIE_DATA_DIR:-./data}/admin-token"
  ```

- **`exec`** -- pipes the token to an operator-configured command via
  **stdin only** (never argv or env, so it can't leak via `ps` or
  `/proc/<pid>/environ`). A non-zero exit is a delivery failure -- see
  below for what that aborts, depending on which command triggered it.
  This is the general "push into my secret store" mechanism -- e.g.
  writing straight into Vault:
  ```bash
  export MAGPIE_ADMIN_TOKEN_SINK=exec
  export MAGPIE_ADMIN_TOKEN_SINK_EXEC_COMMAND='vault kv put -mount=secret bootstrap/magpie/admin-token token=-'
  docker compose -f docker-compose.yml up -d
  ```

- **`discard`** -- generates and immediately drops the bootstrap token;
  nothing is delivered anywhere. Use this for an "install now, mint a token
  when I'm ready" flow. Obtain a usable admin-scope token later via an
  interactive `docker exec` session (which prints to your terminal, not the
  container log stream, so it's safe):
  ```bash
  export MAGPIE_ADMIN_TOKEN_SINK=discard
  docker compose -f docker-compose.yml up -d
  docker compose -f docker-compose.yml exec magpie \
    magpie-ctl token create --name ops-admin --scope admin
  ```

- **`stdout`** -- opt-in only, prints a warning plus the token to stdout
  (the pre-#387 behavior). Never the default; only use this for local/dev
  convenience, not production:
  ```bash
  export MAGPIE_ADMIN_TOKEN_SINK=stdout
  docker compose -f docker-compose.yml up -d
  docker compose -f docker-compose.yml logs magpie | grep "ADMIN TOKEN"
  ```

Token generation happens **only** on first boot. In every case (first-boot
init, `--reset-admin-token`, and `token rotate admin`) delivery is attempted
**before** any database change -- a generated-but-undelivered token is a
liability, not a degraded feature, so nothing is ever persisted or revoked
until the sink confirms success:

- **First boot:** a `file`/`exec` delivery failure means no admin token
  exists in the database, and the server does not start (entrypoint.sh
  removes the incomplete database and retries automatically on next start).
- **`--reset-admin-token` / `token rotate admin`** (run via `docker exec`
  against an already-running server): a delivery failure leaves the
  **existing** admin token completely untouched -- nothing is lost. Fix the
  sink and simply re-run the command.

A native `vault` sink (writing directly to a Vault path, optionally with
response-wrapping) is planned for a future release; `exec` already covers
Vault via `vault kv put` in the meantime.

## First Steps

Once the admin token has been delivered (see above), use it to bootstrap
everyday access:

1. Create CI tokens:
```bash
docker compose -f docker-compose.yml exec magpie magpie-ctl token create --name ci-deployer --scope write
```

2. Install client:
```bash
uv pip install git+https://github.com/SouthwestCCDC/magpie
```

3. Configure client (using whatever hostname/TLS your reverse proxy fronts
   magpie with -- magpie itself has no domain of its own):
```bash
export MAGPIE_SERVER=https://magpie.example.com
export MAGPIE_TOKEN=mgp_your_token_here
```

4. Test:
```bash
curl https://magpie.example.com/health
echo "test" > test.txt
magpie push test.txt --to test/hello
magpie get test/hello:latest
```

## Configuration

Key environment variables (see [.env.example](../.env.example) for all):
- `MAGPIE_DATA_DIR` - Storage path (default: `./data`)
- `MAGPIE_IMAGE` - Image to run (default: `ghcr.io/southwestccdc/magpie:latest`)
- `MAGPIE_HTTP_PORT` - Host port published for the container's plain-HTTP `:8080` (default: 8080)
- `MAGPIE_BIND_IP` - Host IP the published port binds to (default: all interfaces)
- `MAGPIE_STORAGE_PATH` - Artifact storage (default: `/data/artifacts`)
- `MAGPIE_RETENTION_DAYS` - GC retention period (default: 90)
- `MAGPIE_ADMIN_TOKEN_SINK` - Admin bootstrap token delivery: `file`/`exec`/`discard`/`stdout` (required, no default; see [Admin Token Delivery](#admin-token-delivery) above)
- `MAGPIE_ALLOWED_CIDRS` - CIDR ranges allowed to bypass auth for read-only access (default: none)
- `MAGPIE_TRUSTED_PROXIES` - IPs/CIDRs whose `X-Forwarded-For` the bundled Caddy trusts (default: none; see [Trusted Proxies](#trusted-proxies) below)

## Trusted Proxies

The bundled Caddy determines the client IP used by `MAGPIE_ALLOWED_CIDRS`
(and shown in access logs) from the real TCP connection, unless
`MAGPIE_TRUSTED_PROXIES` names the immediate peer as a trusted proxy -- in
which case it instead reads the client IP from that peer's
`X-Forwarded-For` header.

**Default is empty: no proxy is trusted.** Magpie serves plain HTTP only
and is always expected to sit behind an external reverse proxy for TLS
(see [Production Deployment](#production-deployment) above) -- set
`MAGPIE_TRUSTED_PROXIES` to that proxy's exact address(es), scoped as
tightly as possible, never a broad range. Any client positioned within a
trusted range can set `X-Forwarded-For` and have Caddy believe it, which
would let it satisfy `MAGPIE_ALLOWED_CIDRS` without a bearer token:

```bash
export MAGPIE_TRUSTED_PROXIES="10.3.3.10"
docker compose -f docker-compose.yml up -d
```

The [`magpie-deploy.sh` installer](../scripts/magpie-deploy.sh) prompts
for this interactively during `install`, and accepts `--trusted-proxies`
on both `install` and `update`. If `MAGPIE_ALLOWED_CIDRS` is configured
with no `MAGPIE_TRUSTED_PROXIES` ever set, the installer gates on it --
`update` hard-fails (bypass: `--accept-empty-trusted-proxies`); `install`
only warns, since a fresh install has nothing running yet to lock anyone
out of. See issue [#575](https://github.com/SouthwestCCDC/magpie/issues/575)
and issue [#579](https://github.com/SouthwestCCDC/magpie/issues/579).

## Deprecated in v0.2.0

The bundled single-container image ([issue #309](https://github.com/SouthwestCCDC/magpie/issues/309)'s
2026-07-18 decision) has no in-container TLS and no operator-facing
Caddyfile, so the following `magpie-deploy.sh` flags no longer do
anything. They are still accepted (with a deprecation warning on stderr)
for compatibility with existing scripts/playbooks -- passing one does not
break the install/update -- but will be **rejected outright starting in
v0.3.0**:

- `--tls-mode`, `--domain`, `--tls-cert`, `--tls-key`, `--https-port`, `--acme-server`

Remove them from any Ansible playbooks or wrapper scripts that still pass
them, and front magpie with your own reverse proxy for TLS instead (see
[Production Deployment](#production-deployment) above).

## Upgrading to v0.2.0

v0.2.0 replaces the two-container topology (a `caddy` sidecar in front of
the `magpie` FastAPI service) with a single bundled container (Caddy +
uvicorn supervised together, see `Dockerfile.bundled`). `magpie-deploy.sh
update` performs this swap automatically, wrapped in an automatic
backup/assert/rollback safety envelope ([issue
#561](https://github.com/SouthwestCCDC/magpie/issues/561)):

1. **Backs up** the data directory (`magpie.db`, `.env`, `admin-token`,
   and -- by default, best-effort -- the artifacts tree) to
   `<install>/backups/<version>-<UTC timestamp>/` before touching
   anything -- automatic, no manual backup step needed. The default
   artifact mode (`--backup-artifacts=link`) hardlinks the artifacts tree
   and falls back to *skipping* it (not copying) if hardlinking fails,
   e.g. when the data directory is on a different filesystem than the
   backup location; `MANIFEST`'s `backup_artifacts_mode` in each backup
   records what actually happened. See [Backup &
   Restore](backup-restore.md#automatic-pre-update-backups) for the
   layout and the flags that control it.
2. **Swaps** the topology (repo checkout, `docker-compose.yml`, `.env`
   reconcile, image pull, systemd units) as before.
3. **Asserts** the new container is actually healthy and correctly
   serving: health check, an existing tagged artifact still downloads
   byte-identical (SHA-256 verified), an existing token still
   authenticates, tags still resolve, and the token count is unchanged.
4. **Rolls back automatically** on any assertion failure -- restores the
   prior `.env`/`docker-compose.yml`/systemd units/image/data and
   restarts, so a bad update never leaves the install silently broken or
   boot-looping. Disable with `--no-rollback` if you'd rather investigate
   the failed new stack in place.

The data directory (`MAGPIE_DATA_DIR`) is a bind mount and is not touched
by the container swap itself; the backup/assert/rollback envelope above
is what makes the swap safe to run unattended.

The artifact byte-identity/tag-resolution and token-authentication checks
in step 3 reach the pre-update two-container install through its own
`caddy` container (matching this project's own `--tls-mode off`
real-world deployments); a prior install using `--tls-mode auto`/`manual`
may not be reachable this way, in which case those specific checks are
skipped (logged) rather than failing the update. Health, the
data-format version, and the token *count* (checked directly via
`magpie-ctl`, not through Caddy) still gate every upgrade regardless
(see [issue #603](https://github.com/SouthwestCCDC/magpie/issues/603)).

If this install was using built-in TLS termination (`--tls-mode
auto`/`manual`, persisted as `TLS_MODE=auto`/`manual` in
`<install>/etc/.env`), `update` refuses to proceed until you acknowledge
the change: the bundled image never terminates TLS, so after updating,
magpie serves plain HTTP on `MAGPIE_HTTP_PORT` only.

1. Put a reverse proxy in front of magpie for HTTPS (see [Production
   Deployment](#production-deployment) above) before updating, if you
   don't already have one.
2. Re-run `magpie-deploy.sh update --accept-builtin-tls-removed` to
   acknowledge and proceed.

An install that was already `--tls-mode off` (HTTP-only, already fronted)
needs no acknowledgment -- nothing changes for it beyond the topology
swap. `update` also strips the now-dead `MAGPIE_DOMAIN`, `MAGPIE_HTTPS_PORT`,
`TLS_MODE`, and `ACME_SERVER` keys from `<install>/etc/.env` (logging each
removal), and adds `MAGPIE_IMAGE`/`MAGPIE_ADMIN_TOKEN_SINK` if this install
predates them -- the canonical compose file has no default for
`MAGPIE_ADMIN_TOKEN_SINK`, so a pre-v0.2.0 `.env` without it would
otherwise boot-loop.

See [Trusted Proxies](#trusted-proxies) above for the (unrelated, still
current) `MAGPIE_TRUSTED_PROXIES`/`MAGPIE_ALLOWED_CIDRS` gate that `update`
also checks.

Next: [Production Checklist](production-checklist.md) → [User Guide](user-guide.md) → [Backup & Restore](backup-restore.md)

---

*(AI-generated via Claude Code w/ Sonnet 4.5; updated for the v0.2.0 bundled-image topology via Claude Code w/ Opus 4.8)*
