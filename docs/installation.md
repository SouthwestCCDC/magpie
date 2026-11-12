# Installation Guide

Magpie has **two independent things to install**: the *server* (a container, on a host you
control) and the *client* (the `magpie` CLI, on every workstation and CI runner that pushes or
pulls). You do not need the server checkout to use the client, and a server host does not need
the CLI installed on the host itself -- `magpie-ctl` runs inside the container.

| | Server | Client |
|---|---|---|
| What | Bundled container (Caddy + FastAPI) | `magpie` CLI (and `magpie-ctl`, unused on a client) |
| Installed on | One Linux host, behind your reverse proxy | Laptops, CI runners, deploy hosts |
| How | Release assets with digest-pinned Docker Compose (primary); installer optional | `uv tool install` |
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

### Option A: release assets with plain Compose (recommended)

```bash
base=https://github.com/SouthwestCCDC/magpie/releases/latest/download
curl -fsSLO "$base/docker-compose.yml" -fsSLO "$base/env.example" -fsSLO "$base/SHA256SUMS"
grep -E ' (docker-compose\.yml|env\.example)$' SHA256SUMS | sha256sum -c
cp env.example .env
echo 'MAGPIE_ADMIN_TOKEN_SINK=file' >> .env
# Edit .env for your data directory, runtime uid/gid, and trusted proxy.
docker compose up -d
```

Each release's `docker-compose.yml` pins that release's bundled image by digest; the matching
`env.example` is verified against `SHA256SUMS`. Keep the files together and do not replace the
pinned image with a mutable tag. See [Release Assets](releases.md#release-assets) and the
[Quick Start Guide](quickstart.md) for the full walkthrough. Magpie serves plain HTTP on
`:8080`; put an external reverse proxy in front of it for TLS.

### Option B: optional installer

The installer is a convenience for bare-metal or VM hosts where you want systemd units and
account management:

```bash
git clone https://github.com/SouthwestCCDC/magpie
cd magpie
sudo ./scripts/magpie-deploy.sh install --noninteractive
```

It clones the repository, pins a released bundled image, writes configuration under
`<install dir>/etc/.env`, and installs systemd units. It defaults
`MAGPIE_ADMIN_TOKEN_SINK=file`; see the [Quick Start Guide](quickstart.md).

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
MAGPIE_SERVER=${MAGPIE_SERVER:-http://localhost:8080}
# Client and server minor versions must match.
V="$(curl -fsS "${MAGPIE_SERVER}/health" | sed -E 's/.*"version" *: *"([^"]+)".*/\1/; s/^([0-9]+\.[0-9]+\.[0-9]+)(a|b|rc)([0-9]+)$/\1-\2\3/')"
uv tool install "git+https://github.com/SouthwestCCDC/magpie@v${V}"
```

That puts `magpie` and `magpie-ctl` in `~/.local/bin` in their own isolated environment, with
uv fetching Python 3.13 if the host doesn't have it. Set `MAGPIE_SERVER` to the running server
URL before this command if it is not at `http://localhost:8080`.

**Match the client's minor version to the server's.** The server reads the CLI's `User-Agent`
and refuses an older client with `426 Upgrade Required`:

```
Error: Upload failed (426): magpie-cli 0.1.6 is not compatible with server 0.2.0.
Minimum required client version: 0.2.0.
```

To upgrade later, re-run the health-derived install command above with `--force`. See
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
# `magpie status` needs an ADMIN-scope token (the break-glass admin token above);
# the write/read tokens minted in step 2 are rejected here.
magpie status                              # server version, storage used, artifact count

# These work with the write-scope token (push/tag) or read-scope token (ls/get):
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
`MAGPIE_IMAGE` (the release asset pins it by digest), `MAGPIE_DATA_DIR`, and `MAGPIE_HTTP_PORT` /
`MAGPIE_BIND_IP`. For `MAGPIE_ALLOWED_CIDRS`, comma-separated is the documented form; whitespace
or mixed separators are also accepted. For a client: `MAGPIE_SERVER` and `MAGPIE_TOKEN`.

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

- **`file`** -- writes an owner-only (0600) file, owned by the uid the
  container runs as (see [Runtime user](#runtime-user)), at
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
  # -A1: the token is printed on the line after the "ADMIN TOKEN" label
  docker compose -f docker-compose.yml logs magpie | grep -A1 "ADMIN TOKEN"
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

### Site-specific compose settings

On an install made by `magpie-deploy.sh`, put site-specific compose
settings (service uid/gid, `cap_drop`/`cap_add`, extra environment) in
`<install>/docker-compose.override.yml`. Every compose command the
installer and its systemd units run uses `docker-compose.yml` plus that
file when it exists, so creating or editing it only needs
`systemctl restart magpie.service`. `update` never touches it. For manual
compose commands, use `<install>/bin/magpie-compose <subcommand>`, which
applies the same file set. A copy of the repository's development override
is refused, not merged. The compose project name stays pinned to the
install directory's name, so a top-level `name:` in the override is ignored.

### Runtime user

After a brief root prelude for ownership fixups, the supervised processes
run as `MAGPIE_UID`:`MAGPIE_GID`. On start, the container chowns the data
directory itself (not its contents) to that uid, so a fresh data directory
needs no preparation. With both empty, the container runs as the data
directory's current owner, which is root for a directory Docker created.

The canonical Compose file drops all capabilities, then adds only
`SETUID`/`SETGID` for the privilege drop and `CHOWN`/`DAC_OVERRIDE` for
first-start ownership setup. It also uses a read-only root filesystem with
writable tmpfs mounts at `/run`, `/tmp`, and `/var/lib/caddy`. Starting
directly as a non-root user skips the root prelude; all capabilities can
then be dropped, provided the mounted data directory is already writable.

- **Release assets / plain compose:** `env.example` sets both to `10001`.
- **`magpie-deploy.sh install`:** a fresh install creates a `magpie` system
  account, or reuses an existing one (e.g. one config management pinned to
  a fixed uid), writes its uid/gid to `<install>/etc/.env`, and chowns the
  new data directory to it. Installing onto a data directory that already
  holds data keeps that directory's owner. A plain `uninstall` keeps the
  data and its account; `uninstall --purge` removes the account only if the
  installer created it (recorded in `/var/lib/magpie-deploy/created-accounts`).
- **Existing deployments** keep running as the uid they used before.
  `update` never changes it, and warns when it is root.

To move an existing root-owned deployment to a dedicated account:

```bash
systemctl stop magpie                    # plain compose: docker compose down
id magpie || useradd --system --no-create-home --shell /usr/sbin/nologin magpie
chown -R magpie:magpie /path/to/data     # MAGPIE_DATA_DIR
# Set MAGPIE_UID / MAGPIE_GID to `id -u magpie` / `id -g magpie` in
# <install>/etc/.env (plain compose: your .env).
systemctl start magpie                   # plain compose: docker compose up -d
```

Don't set a new uid without the `chown`: the container can't write the
existing root-owned `magpie.db` and artifacts.

#### Starting without root (e.g. Kubernetes)

The container can also be started directly as its runtime uid
(`docker run --user 10001:10001`, or Kubernetes `runAsUser` with
`runAsNonRoot: true`), skipping the root step entirely. All capabilities
can then be dropped. In that mode:

- `MAGPIE_UID` / `MAGPIE_GID` are not used, and nothing can chown, so the
  data directory must already be writable by that uid. On Kubernetes, set
  `fsGroup` to the gid.
- With a read-only root filesystem, mount a writable `/tmp` (Kubernetes:
  an `emptyDir`). Caddy keeps its state there, and `magpie-ctl sync` needs
  it.
- `docker exec` / `kubectl exec` already run as that uid, so `magpie-ctl`
  works without any privilege drop.

Run one replica only: the database and artifacts live on one volume
(`ReadWriteOnce`), and two containers must never serve the same data.

### Choosing what `update` installs

`magpie-deploy.sh update` with no target moves to the latest published,
non-prerelease GitHub release (`releases/latest`), never the default
branch's HEAD. `update --release <tag>` moves to exactly that tag, including prereleases.
`update --branch <name>`
tracks a branch's HEAD and is meant only for development. If the release
lookup fails (e.g. GitHub's unauthenticated API rate limit), `update`
stops with an error instead of falling back. To move to an older version,
pass `--accept-downgrade`; `magpie-ctl migrate` still refuses data in a
newer format than the target supports (see
[Downgrading the data format](#downgrading-the-data-format) to revert it
first). The installed release tag, commit and image are recorded in
`<install>/etc/.env` (`MAGPIE_RELEASE_*`, `MAGPIE_IMAGE`) and shown by
`magpie-deploy.sh status`.

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

In particular, `--tls-mode off` is accepted and ignored; it was already the HTTP-only mode.

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

**Data-format migration happens at container start.** On every start,
before it serves traffic, the bundled container brings `magpie.db` to the
data format its build supports: `magpie-ctl init` on a fresh volume,
otherwise `magpie-ctl migrate` (a quiet no-op when already current). This
runs under the same `.magpie-init.lock` as first-boot init, so concurrent
starts against one data directory are serialized, and each migration is
applied in a single transaction. Before a migration changes anything, a
copy of `magpie.db` is saved to `<data>/backups/` (the newest 3 are kept);
if that copy can't be written, the container refuses to migrate. If
migration fails, or the database is
newer than the image supports (e.g. after rolling the image tag back),
the container exits non-zero without serving and without modifying the
database; run a newer image or restore a backup. Upgrading by changing
the image tag and restarting (plain `docker compose`, Ansible) is
therefore migrated the same way, with that copy of the database but no
automatic rollback; the copy is on the same volume, so
[back up](backup-restore.md#backup-procedures) the data directory too. `update` still runs `magpie-ctl migrate` explicitly after the swap
(by then a no-op) as part of its post-update checks.

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

### Downgrading the data format

Container startup only ever migrates forward. To go back to an older
image whose data format is lower, revert the data with the *newer* image
first -- only it knows how to undo its own migration steps:

The commands below use `docker compose -f docker-compose.yml` (never the
bare form, which merges the development override). On an installer-managed
host, run `<install>/bin/magpie-compose` in its place, and `MAGPIE_IMAGE`
lives in `<install>/etc/.env`.

1. [Back up](backup-restore.md#backup-procedures) the data directory.
2. Find the older image's data-format version N: set `MAGPIE_IMAGE` to the
   older image and run `docker compose -f docker-compose.yml up -d`. It
   refuses to start against the newer data, exits without modifying
   anything, and logs `current: N`
   (`docker compose -f docker-compose.yml logs magpie`). Then set
   `MAGPIE_IMAGE` back to the newer image and run `up -d` again.
3. With the newer image running, revert to N, then immediately switch the
   image and restart (a restart of the newer image would migrate forward
   again):

   ```bash
   docker compose -f docker-compose.yml exec magpie magpie-ctl migrate --to N
   # set MAGPIE_IMAGE to the older image, then:
   docker compose -f docker-compose.yml up -d
   ```

`migrate --to N` reverts every step above N, newest first, in a single
transaction under the same write lock as forward migration, and writes the
new version stamp last. Like forward migration, it first saves a copy of
`magpie.db` to `<data>/backups/`. It changes nothing and exits non-zero if N is above
the current version or not a version that image knows, or if a step in the
way is marked irreversible -- in that case,
[restore a backup](backup-restore.md#restore-procedures) taken before the
upgrade instead. `magpie-deploy.sh update` does not do this for you.


---

*(AI-generated via Claude Code w/ Sonnet 4.5; updated for the v0.2.0 bundled-image topology via Claude Code w/ Opus 4.8; restructured into server/client installation paths via Devin; refreshed for v0.2.0 final via Devin; latest-release URLs, client version guidance, and health-derived install updated via Devin; prerelease tag mapping updated via Devin; multi-CIDR separators clarified for #612 via Devin)*
