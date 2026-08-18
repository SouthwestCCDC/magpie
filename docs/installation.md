# Installation Guide

Magpie has **two independent things to install**: the *server* (a container, on a host you
control) and the *client* (the `magpie` CLI, on every workstation and CI runner that pushes or
pulls). You do not need the server checkout to use the client, and a server host does not need
the CLI installed on the host itself -- `magpie-ctl` runs inside the container.

| | Server | Client |
|---|---|---|
| What | Bundled container (Caddy + FastAPI) | `magpie` CLI (and `magpie-ctl`, unused on a client) |
| Installed on | One Linux host, behind your reverse proxy | Laptops, CI runners, deploy hosts |
| How | [`scripts/magpie-deploy.sh`](../scripts/magpie-deploy.sh), or Docker Compose | `uv tool install` |
| Needs | Docker + Compose v2, root | `uv` (it brings its own Python 3.13) |
| Jump to | [Server installation](#server-installation) | [Client installation](#client-installation) |

For the fastest possible look at Magpie on a throwaway local stack, use the [5-Minute Quick
Start](../README.md#5-minute-quick-start) in the README instead. For a guided real deployment,
see the [Quick Start Guide](quickstart.md). Every environment variable mentioned on this page
is defined once, in the [Configuration Reference](configuration.md).

## Prerequisites

**Server host:** Linux with Docker Engine and Compose v2 (`docker compose version`), root or
`sudo`, a data directory on a filesystem with room for your artifacts, and a reverse proxy
(nginx, Caddy, cloud LB) to terminate TLS in front of it. Magpie itself serves plain HTTP.

**Client:** [`uv`](https://docs.astral.sh/uv/). Nothing else -- no system Python 3.13 required.

## Server installation

### Option A: the installer (recommended)

```bash
git clone https://github.com/SouthwestCCDC/magpie
cd magpie
sudo ./scripts/magpie-deploy.sh install
```

This resolves and pins the correct `<version>-bundled` image, writes `<install dir>/etc/.env`,
installs systemd units, and defaults `MAGPIE_ADMIN_TOKEN_SINK=file`. Add `--noninteractive` for
unattended runs; it prompts for `--trusted-proxies` interactively otherwise. See the
[Quick Start Guide](quickstart.md) for the full walkthrough.

### Option B: Docker Compose by hand

For advanced or non-systemd deployments:

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
source, defaults the admin-token sink, exposes the test-endpoint toggle), which is
correct for a [local development stack](#option-c-local-development-stack)
but must never run in production. Every `docker compose` command on this
page pins it the same way.

Magpie serves plain HTTP only on `MAGPIE_HTTP_PORT` (default 8080) -- it
does not terminate TLS itself. Put a reverse proxy (nginx, another Caddy, a
cloud load balancer, infra-deployment's outer Caddy, etc.) in front of it
for HTTPS. This is also what
[`scripts/magpie-deploy.sh`](../scripts/magpie-deploy.sh) (the recommended
installer for a bare-metal/VM host) deploys -- see the
[Quick Start Guide](quickstart.md).

### Option C: local development stack

```bash
git clone https://github.com/SouthwestCCDC/magpie
cd magpie
docker compose up -d --build      # `just up` runs the same thing in the foreground
```

A bare `docker compose` command merges `docker-compose.override.yml` automatically: it builds
the image from `Dockerfile.bundled` and defaults `MAGPIE_ADMIN_TOKEN_SINK=file`, so no
environment setup is needed. The server is at `http://localhost:8080` and the admin token at
`./data/admin-token`. Compose still warns that `MAGPIE_ADMIN_TOKEN_SINK` "is not set" while
interpolating the canonical file -- harmless here, since the overlay supplies it.

Never use this path in production: it builds an unpinned image from your working tree.

## Client installation

```bash
uv tool install git+https://github.com/SouthwestCCDC/magpie
```

That puts `magpie` and `magpie-ctl` in `~/.local/bin` in their own isolated environment, with
uv fetching Python 3.13 if the host doesn't have it. To pin a release instead of tracking the
`default` branch:

```bash
uv tool install 'git+https://github.com/SouthwestCCDC/magpie@vX.Y.Z'
```

**Match the client's minor version to the server's.** The server reads the CLI's `User-Agent`
and refuses an older client with `426 Upgrade Required`:

```
Error: Upload failed (426): magpie-cli 0.1.6 is not compatible with server 0.2.0.
Minimum required client version: 0.2.0.
```

Upgrade with `uv tool install --force git+https://github.com/SouthwestCCDC/magpie`. See
[API Compatibility](api-compatibility.md#version-coupling).

## First-time configuration

### 1. Retrieve the admin token

See [Admin Token Delivery](#admin-token-delivery) below. With the default `file` sink:

```bash
sudo cat "${MAGPIE_DATA_DIR:-./data}/admin-token"
```

### 2. Mint the tokens you'll actually use

The admin token is break-glass; issue scoped tokens for day-to-day work. `magpie-ctl` runs
inside the container, and prints each token once to your terminal (not to the container logs):

```bash
docker compose -f docker-compose.yml exec magpie \
  magpie-ctl token create --name ci-deployer --scope write
docker compose -f docker-compose.yml exec magpie \
  magpie-ctl token create --name ci-reader --scope read
docker compose -f docker-compose.yml exec magpie magpie-ctl token list
```

(The installer's units use `<install dir>/docker-compose.yml`; run the same commands from there.)

### 3. Point the client at the server

Use the hostname your reverse proxy fronts Magpie with -- Magpie has no domain of its own:

```bash
export MAGPIE_SERVER=https://magpie.example.com
export MAGPIE_TOKEN=mgp_your_token_here
```

Or persist them (server URL and token only) in `~/.magpie/config.toml`:

```bash
magpie config --server https://magpie.example.com --token mgp_your_token_here
```

## Verification

From the server host:

```bash
curl http://localhost:8080/health          # {"status":"ok","version":"..."}
```

From a client, with `MAGPIE_SERVER` and `MAGPIE_TOKEN` set:

```bash
magpie status                              # server version, storage used, artifact count
echo "test" > test.txt
magpie push test.txt --to test/hello
magpie ls test/hello
magpie get test/hello:latest -o roundtrip.txt && cat roundtrip.txt
```

Without `-o`, `magpie get` names the downloaded file after the artifact (`hello`), not after the
file that was uploaded -- content is addressed by hash and the original filename is not stored.

## Configuration

See the **[Configuration Reference](configuration.md)** -- it is the single source of truth for
every Magpie environment variable, including a "what do I actually need?" table.
[`.env.example`](../.env.example) is the commented template to copy into a real `.env`.

The short answer for a server: `MAGPIE_ADMIN_TOKEN_SINK` (required),
`MAGPIE_IMAGE` (pin a `-bundled` release), `MAGPIE_DATA_DIR`, and `MAGPIE_HTTP_PORT` /
`MAGPIE_BIND_IP`. For a client: `MAGPIE_SERVER` and `MAGPIE_TOKEN`.

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

## Trusted Proxies

The bundled Caddy determines the client IP used by `MAGPIE_ALLOWED_CIDRS`
(and shown in access logs) from the real TCP connection, unless
`MAGPIE_TRUSTED_PROXIES` names the immediate peer as a trusted proxy -- in
which case it instead reads the client IP from that peer's
`X-Forwarded-For` header.

**Default is empty: no proxy is trusted.** Magpie serves plain HTTP only
and is always expected to sit behind an external reverse proxy for TLS
(see [Server installation](#server-installation) above) -- set
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
[Server installation](#server-installation) above).

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

The artifact byte-identity/tag-resolution checks in step 3 reach the
pre-update two-container install through its own `caddy` container
(matching this project's own `--tls-mode off` real-world deployments); a
prior install using `--tls-mode auto`/`manual` may not be reachable this
way, in which case those specific checks are skipped (logged) rather
than failing the update. Health, the data-format version, the token
*count*, and minting the probe token used by the token-authentication
check are all checked/captured directly via `magpie-ctl`, not through
Caddy -- unaffected either way -- and still gate every upgrade
regardless (see [issue
#603](https://github.com/SouthwestCCDC/magpie/issues/603)).

If this install was using built-in TLS termination (`--tls-mode
auto`/`manual`, persisted as `TLS_MODE=auto`/`manual` in
`<install>/etc/.env`), `update` refuses to proceed until you acknowledge
the change: the bundled image never terminates TLS, so after updating,
magpie serves plain HTTP on `MAGPIE_HTTP_PORT` only.

1. Put a reverse proxy in front of magpie for HTTPS (see [Server
   installation](#server-installation) above) before updating, if you
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

Next: [Production Checklist](production-checklist.md) → [Configuration Reference](configuration.md) → [User Guide](user-guide.md) → [Backup & Restore](backup-restore.md)

---

*(AI-generated via Claude Code w/ Sonnet 4.5; updated for the v0.2.0 bundled-image topology via Claude Code w/ Opus 4.8; restructured into server/client installation paths via Devin)*
