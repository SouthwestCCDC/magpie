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
export MAGPIE_DOMAIN=magpie.example.com
export MAGPIE_DATA_DIR=./data
docker compose -f docker-compose.prod.yml up -d
```

Requires: domain name for TLS (auto-provisioned via Let's Encrypt).

## Admin Token Delivery

On first boot (no existing database), magpie generates a break-glass admin
token and delivers it via the sink configured by `MAGPIE_ADMIN_TOKEN_SINK`.
It is **never** printed to stdout (and therefore never captured by
`docker logs` or a log shipper) unless `sink=stdout` is explicitly chosen.

`docker-compose.prod.yml` requires this variable to be set explicitly --
there is no default in production, by design (fail-closed). The dev
`docker-compose.yml` defaults to `sink=file` for frictionless local
bootstrap. See [.env.example](../.env.example) for the full list of
`MAGPIE_ADMIN_TOKEN_SINK*` variables.

Choose one:

- **`file`** -- writes a root-only (0600) file at
  `MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH` (default: a sibling of
  `MAGPIE_DATABASE_PATH`, typically `/data/admin-token`):
  ```bash
  export MAGPIE_ADMIN_TOKEN_SINK=file
  docker compose -f docker-compose.prod.yml up -d
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
  docker compose -f docker-compose.prod.yml up -d
  ```

- **`discard`** -- generates and immediately drops the bootstrap token;
  nothing is delivered anywhere. Use this for an "install now, mint a token
  when I'm ready" flow. Obtain a usable admin-scope token later via an
  interactive `docker exec` session (which prints to your terminal, not the
  container log stream, so it's safe):
  ```bash
  export MAGPIE_ADMIN_TOKEN_SINK=discard
  docker compose -f docker-compose.prod.yml up -d
  docker compose -f docker-compose.prod.yml exec magpie \
    magpie-ctl token create --name ops-admin --scope admin
  ```

- **`stdout`** -- opt-in only, prints a warning plus the token to stdout
  (the pre-#387 behavior). Never the default; only use this for local/dev
  convenience, not production:
  ```bash
  export MAGPIE_ADMIN_TOKEN_SINK=stdout
  docker compose -f docker-compose.prod.yml up -d
  docker compose -f docker-compose.prod.yml logs magpie | grep "ADMIN TOKEN"
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
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl token create --name ci-deployer --scope write
```

2. Install client:
```bash
uv pip install git+https://github.com/SouthwestCCDC/magpie
```

3. Configure client:
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
- `MAGPIE_DOMAIN` - Domain for TLS
- `MAGPIE_DATA_DIR` - Storage path (default: `./data`)
- `MAGPIE_STORAGE_PATH` - Artifact storage (default: `/data/artifacts`)
- `MAGPIE_RETENTION_DAYS` - GC retention period (default: 90)
- `MAGPIE_DEBUG` - Verbose logging (default: false; never in production)
- `MAGPIE_ADMIN_TOKEN_SINK` - Admin bootstrap token delivery: `file`/`exec`/`discard`/`stdout` (required in production, no default; see [Admin Token Delivery](#admin-token-delivery) above)
- `MAGPIE_ALLOWED_CIDRS` - CIDR ranges allowed to bypass auth for read-only access (default: none)
- `MAGPIE_TRUSTED_PROXIES` - IPs/CIDRs whose `X-Forwarded-For` Caddy trusts (default: none; see [Trusted Proxies](#trusted-proxies) below)

## Trusted Proxies

Caddy determines the client IP used by `MAGPIE_ALLOWED_CIDRS` (and shown in
access logs) from the real TCP connection, unless `MAGPIE_TRUSTED_PROXIES`
names the immediate peer as a trusted proxy -- in which case it instead reads
the client IP from that peer's `X-Forwarded-For` header.

**Default is empty: no proxy is trusted.** If Caddy is directly internet-facing
(the standard `docker-compose.prod.yml` setup, terminating TLS itself), leave
this unset -- the real connecting peer's IP is always correct.

Only set `MAGPIE_TRUSTED_PROXIES` if Caddy sits behind another reverse proxy or
load balancer that you control, and scope it to the exact address(es) of that
proxy -- never a broad range. Any client positioned within a trusted range can
set `X-Forwarded-For` and have Caddy believe it, which would let it satisfy
`MAGPIE_ALLOWED_CIDRS` without a bearer token:

```bash
export MAGPIE_TRUSTED_PROXIES="10.3.3.10"
docker compose -f docker-compose.prod.yml up -d
```

The [`magpie-deploy.sh` installer](../scripts/magpie-deploy.sh)'s `--tls-mode
off` mode (Caddy running HTTP-only behind an external proxy) prompts for this
interactively via `--trusted-proxies`; `--tls-mode auto`/`manual` (Caddy
directly facing the internet) always default to trusting nothing. See issue
[#575](https://github.com/SouthwestCCDC/magpie/issues/575).

## Upgrading to v0.1.6

As of v0.1.6, Caddy trusts no proxy by default (see [Trusted
Proxies](#trusted-proxies) above). If your deployment sits behind a reverse
proxy or load balancer and relies on `MAGPIE_ALLOWED_CIDRS` for token-less
reads, this changes the client IP Caddy sees: it now reads the real TCP peer
(your proxy) instead of the original client from `X-Forwarded-For`, so
CIDR-allow stops matching real clients and those reads start returning 401.

`magpie-deploy.sh update` responds to this in two tiers, keyed on
`--tls-mode`:

- **`--tls-mode off`** (Caddy is HTTP-only, almost certainly behind an
  external proxy): if `MAGPIE_TRUSTED_PROXIES` has never been configured for
  this install and `MAGPIE_ALLOWED_CIDRS` is set, `update` treats this as
  high-risk. Interactively, it prompts for the proxy's hop (or a
  confirmation that magpie is directly exposed) before proceeding; declining
  aborts the update. With `--noninteractive`, it hard-fails instead of
  proceeding silently -- see the flags below.
- **`--tls-mode auto`/`manual`** (Caddy faces the internet directly): the
  same `MAGPIE_ALLOWED_CIDRS` set / `MAGPIE_TRUSTED_PROXIES` empty
  combination only prints an advisory warning and the update completes,
  since an empty `MAGPIE_TRUSTED_PROXIES` is the expected, correct value
  here.

This only fires once: as soon as `MAGPIE_TRUSTED_PROXIES` has a value in
`<install>/etc/.env` -- including an explicit empty one -- `update` treats
that as your deliberate choice and stops checking.

If you are fronted (`--tls-mode off`):

1. Determine your proxy's address as seen by magpie's Caddy. Two common ways:
   - `docker network inspect <magpie-network>` and read the `Subnet` field
     (if the proxy hops in via magpie's own docker bridge, this is usually a
     /16, e.g. `172.20.0.0/16`); for a tighter scope, use the `Gateway`
     field instead as a `/32`.
   - Check the inner Caddy access log's `remote_ip` field for a request you
     know came through the proxy.
2. Set `MAGPIE_TRUSTED_PROXIES` to that address, scoped as tightly as you
   can (a `/32` if you know the exact hop; only widen to a subnet like the
   bridge `/16` if the hop varies) -- either via `--trusted-proxies
   <value>` on the `update` command, or by editing
   `<install>/etc/.env` directly, or by answering the interactive prompt.
3. Re-run `magpie-deploy.sh update` (if you edited `.env` by hand) to
   regenerate the Caddyfile.

If magpie is directly exposed (no proxy in front of it), no action is
needed for an interactive `update` -- confirm this at the prompt. For a
non-interactive (e.g. Ansible-driven) `update`, pass
`--accept-empty-trusted-proxies` to acknowledge this and proceed.

Next: [Production Checklist](production-checklist.md) → [User Guide](user-guide.md) → [Backup & Restore](backup-restore.md)

---

*(AI-generated via Claude Code w/ Sonnet 4.5)*
