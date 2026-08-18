# Configuration Reference

**This page is the single source of truth for Magpie's environment variables.**
[`.env.example`](../.env.example) is the commented template you copy into a real `.env`;
[installation.md](installation.md) and [user-guide.md](user-guide.md) link here rather than
repeating the list.

Server-side variables are read by the container (the FastAPI app, the bundled Caddy, or the
container's own startup wrapper). Client-side variables are read by the `magpie` CLI on your
workstation or CI runner. They are separate sets -- a client never needs a server variable,
and vice versa.

## What do I actually need?

### Server

| Variable | Needed? | Why |
|----------|---------|-----|
| `MAGPIE_ADMIN_TOKEN_SINK` | **Required** | How the first-boot admin token is delivered. No default, by design: the container refuses to start without it. Use `file` unless you have a secret store. See [Admin Token Delivery](installation.md#admin-token-delivery). |
| `MAGPIE_IMAGE` | **Required in production** | Pin a released bundled image (`ghcr.io/southwestccdc/magpie:X.Y.Z-bundled`). The compose default (`:latest`) is *not* a bundled image and will not work with this topology. The installer sets this for you. |
| `MAGPIE_DATA_DIR` | Recommended | Host directory holding artifacts, the token database, and the admin-token file. Defaults to `./data` next to `docker-compose.yml`; set an absolute path for a real deployment. |
| `MAGPIE_HTTP_PORT` / `MAGPIE_BIND_IP` | Recommended | Where the container's plain-HTTP `:8080` is published. Bind to a loopback/internal address if your reverse proxy is on the same host. |
| `MAGPIE_TRUSTED_PROXIES` | Only with `MAGPIE_ALLOWED_CIDRS` | The exact address(es) of the proxy in front of Magpie, so client IPs are read from `X-Forwarded-For`. See [Trusted Proxies](installation.md#trusted-proxies). |
| Everything else | Optional | Retention, upload limits, logging, S3 backup, Sentry/OTel. Defaults are sane. |

Nothing else needs setting for a working server. `MAGPIE_STORAGE_PATH`,
`MAGPIE_DATABASE_PATH`, and `MAGPIE_DEBUG` are set by `docker-compose.yml` itself -- you only
override them for unusual layouts.

### Client

| Variable | Needed? | Why |
|----------|---------|-----|
| `MAGPIE_SERVER` | **Required** | Base URL of the server (`https://magpie.example.com`, or `http://localhost:8080` locally). |
| `MAGPIE_TOKEN` | **Required** | Bearer token. Scope depends on what you're doing (`read` to pull, `write` to push). |
| `MAGPIE_TIMEOUT`, `MAGPIE_CA_CERT` | Optional | Long uploads over slow links; private CAs. |

`MAGPIE_SERVER` and `MAGPIE_TOKEN` can live in `~/.magpie/config.toml` instead --
see [Client Setup](user-guide.md#client-setup).

## Client variables

Read by the `magpie` CLI. All can be overridden by CLI flags
(precedence: **CLI flag > environment variable > `~/.magpie/config.toml`**).

| Variable | Default | In `config.toml`? | Description |
|----------|---------|-------------------|-------------|
| `MAGPIE_SERVER` | *(none)* | yes | Server base URL, e.g. `https://magpie.example.com` |
| `MAGPIE_TOKEN` | *(none)* | yes | Bearer token, e.g. `mgp_...` |
| `MAGPIE_TIMEOUT` | `600` | no (deliberately) | Request timeout: seconds, or a duration like `30s`, `5m`, `1h30m` |
| `MAGPIE_CA_CERT` | *(none)* | no | Path to an additional CA certificate for HTTPS verification |

## Server variables

### Required

| Variable | Default | Consumed by | Description |
|----------|---------|-------------|-------------|
| `MAGPIE_ADMIN_TOKEN_SINK` | *(none -- required)* | app | First-boot admin token delivery: `file`, `exec`, `discard`, or `stdout`. Fail-closed: an unset value means the container exits non-zero at boot. See [Admin Token Delivery](installation.md#admin-token-delivery). |

### Deployment (Docker Compose only -- not read by the application)

| Variable | Default | Description |
|----------|---------|-------------|
| `MAGPIE_IMAGE` | `ghcr.io/southwestccdc/magpie:latest` | Image to run. Set to a `X.Y.Z-bundled` tag for any real deployment; the default tag is the pre-v0.2.0 non-bundled image and is incompatible with this compose file. |
| `MAGPIE_DATA_DIR` | `./data` | Host path bind-mounted at `/data` (artifacts, `magpie.db`, `admin-token`) |
| `MAGPIE_HTTP_PORT` | `8080` | Host port published for the container's plain-HTTP `:8080` |
| `MAGPIE_BIND_IP` | `0.0.0.0` | Host IP the published port binds to |

### Container runtime (read by the image's startup wrapper)

| Variable | Default | Description |
|----------|---------|-------------|
| `MAGPIE_UID` | auto-detected from `/data` ownership, else `1000` | UID the supervised processes run as |
| `MAGPIE_GID` | auto-detected from `/data` ownership, else `1000` | GID the supervised processes run as |

### Storage and limits

| Variable | Default | Description |
|----------|---------|-------------|
| `MAGPIE_STORAGE_PATH` | `/data/artifacts` (set by compose) | Artifact storage root inside the container |
| `MAGPIE_TEMP_PATH` | `{storage}/.tmp` | Upload staging directory |
| `MAGPIE_DATABASE_PATH` | `/data/magpie.db` | SQLite database (tokens only). Must be an absolute path. |
| `MAGPIE_RETENTION_DAYS` | `90` | Days an untagged blob survives before it is GC-eligible |
| `MAGPIE_MAX_UPLOAD_SIZE` | *(unlimited)* | Maximum upload size in bytes |

### Admin token delivery

| Variable | Default | Description |
|----------|---------|-------------|
| `MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH` | `/data/admin-token` | Destination for `sink=file` (written `0600`) |
| `MAGPIE_ADMIN_TOKEN_SINK_EXEC_COMMAND` | *(none)* | Command for `sink=exec`; the token is piped to its **stdin** (never argv/env). Parsed with `shlex`, not run through a shell. |
| `MAGPIE_ADMIN_TOKEN_SINK_EXEC_TIMEOUT_SECONDS` | `30` | Timeout for the `sink=exec` command |

### Logging, debug, and observability

| Variable | Default | Description |
|----------|---------|-------------|
| `MAGPIE_LOG_FORMAT` | `json` | `json` for structured logs, `console` for human-readable |
| `MAGPIE_DEBUG` | `false` (**hardcoded off** by `docker-compose.yml`) | Verbose errors; deliberately not operator-settable in the canonical compose file |
| `MAGPIE_SENTRY_DSN` | *(none)* | Sentry DSN for server-side error tracking |
| `MAGPIE_SENTRY_TRACES_SAMPLE_RATE` | `1.0` | Sentry performance sampling rate (0.0-1.0) |
| `MAGPIE_OTEL_ENABLED` | `false` | Enable OpenTelemetry tracing |
| `MAGPIE_OTEL_ENDPOINT` | *(none)* | OTLP collector endpoint (required when OTel is enabled) |
| `MAGPIE_OTEL_SERVICE_NAME` | `magpie` | Service name reported in traces |

### Access control (consumed by the bundled Caddy)

| Variable | Default | Description |
|----------|---------|-------------|
| `MAGPIE_ALLOWED_CIDRS` | *(empty -- disabled)* | **One** CIDR range whose clients may read artifacts without a bearer token (writes and admin operations still require one). See the limitation below. |
| `MAGPIE_TRUSTED_PROXIES` | *(empty -- trust no proxy)* | Space-separated IPs/CIDRs whose `X-Forwarded-For` header Caddy honors when deciding the client IP used by `MAGPIE_ALLOWED_CIDRS`. Scope this to the exact upstream hop(s), never a broad range. See [Trusted Proxies](installation.md#trusted-proxies). |

> **Limitation (v0.2.0):** `MAGPIE_ALLOWED_CIDRS` accepts only a *single* range in the bundled
> image. A comma-separated list fails Caddy's config load, and a space-separated list fails the
> application's settings validation -- either way the container exits at boot instead of
> starting with a partially-applied allow-list. Use one range (widen it, or front Magpie with a
> proxy that does the allow-listing) until this is fixed; see issue
> [#612](https://github.com/SouthwestCCDC/magpie/issues/612).

### S3 backup (optional)

Used by `magpie-ctl sync`; see [Backup & Restore](backup-restore.md).

| Variable | Default | Description |
|----------|---------|-------------|
| `MAGPIE_S3_BUCKET` | *(none -- disabled)* | Bucket for artifact backup |
| `MAGPIE_S3_PREFIX` | *(empty)* | Key prefix, e.g. `magpie/backups` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | *(none)* | Credentials (or use an instance role) |
| `AWS_SESSION_TOKEN` | *(none)* | Session token for temporary (STS) credentials |
| `AWS_DEFAULT_REGION` | *(none)* | Bucket region |

### Development only

| Variable | Default | Description |
|----------|---------|-------------|
| `MAGPIE_ENABLE_TEST_ENDPOINTS` | `false` | Enables `/api/v1/_test/*` memory-tracking endpoints used by the e2e suite. Set only by `docker-compose.override.yml`; never enable in a real deployment. |

### Removed in v0.2.0

The bundled image never terminates TLS, so these no longer exist. `magpie-deploy.sh update`
strips them from an upgraded install's `.env`; remove them from your own `.env` and playbooks.

| Variable | Replacement |
|----------|-------------|
| `MAGPIE_HTTPS_PORT`, `MAGPIE_DOMAIN`, `TLS_MODE`, `ACME_SERVER` | Terminate TLS on your own reverse proxy in front of `MAGPIE_HTTP_PORT` |
| `AUTHENTIK_HOST` | Not consumed by the bundled image (it configured the old operator-facing `Caddyfile.prod`, which no longer exists). Do browser SSO at your external proxy; see [Authentik SSO](authentik-setup.md). |

## Where configuration lives

| Layer | File | Notes |
|-------|------|-------|
| Server, real deployment | `<install dir>/etc/.env` (installer) or `.env` next to `docker-compose.yml` | Copy [`.env.example`](../.env.example) and edit. Compose reads `.env` automatically. |
| Server, ad-hoc | shell environment | `export MAGPIE_...` before `docker compose up`. Compose warns `The "MAGPIE_ADMIN_TOKEN_SINK" variable is not set` if it can't resolve that key from either source -- harmless when an override file supplies it, but a boot failure otherwise. |
| Client | `~/.magpie/config.toml`, environment, or CLI flags | `magpie config --show` prints the effective values and where each came from (it needs a config file to exist -- create one with `magpie config --server ... --token ...`) |
