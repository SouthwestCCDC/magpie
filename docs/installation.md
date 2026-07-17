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

Next: [Production Checklist](production-checklist.md) → [User Guide](user-guide.md) → [Backup & Restore](backup-restore.md)

---

*(AI-generated via Claude Code w/ Sonnet 4.5)*
