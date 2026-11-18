# Architecture

How a Magpie deployment is put together, and why. For how the storage model behaves
day to day (content addressing, tags, garbage collection), see the
[User Guide](user-guide.md).

## The short version

Since v0.2.0 Magpie bundles **Caddy and FastAPI in one container** with a single persistent
data directory:

```
  magpie CLI / curl / CI
        |
        v  HTTPS
  your reverse proxy (nginx, Caddy, cloud LB)  --  terminates TLS
        |
        v  HTTP :8080
  +------------------------ Magpie container -----------------------+
  | Caddy                                                           |
  |   |-- /artifacts/* -----> files from /data/artifacts            |
  |   |                       (access check on protected paths)     |
  |   |                                                             |
  |   `-- auth checks, API, /health --> FastAPI (127.0.0.1:8000)    |
  |                                         |                       |
  |                                         `--> /data              |
  +-----------------------------------------------------------------+
              /data (host bind mount)
                artifacts/  (blobs, manifests, metadata, tag symlinks)
                magpie.db   (SQLite token database)
```

| Piece | Where it lives | Responsibility |
|-------|----------------|----------------|
| Caddy | inside the image, config baked in ([Caddyfile](../docker/bundled/Caddyfile)) | Listens on `:8080`; gates protected routes, serves files from `/data`, and proxies API and health requests to FastAPI |
| FastAPI / uvicorn | inside the image, bound to `127.0.0.1:8000` | Uploads, hashing and dedupe, tag mutations, metadata, token management, GC, and the auth decisions Caddy defers to |
| `/data` | host bind mount (`MAGPIE_DATA_DIR`) | `artifacts/` (blobs + tag symlinks), `magpie.db` (tokens), `admin-token` (with the file sink) |
| Your reverse proxy | outside Magpie entirely | TLS, HSTS, hostnames, and any org-wide access control |

## Why Caddy serves the files

Download response bodies do not pass through Python. Caddy serves the blob from disk while
FastAPI handles token validation and application operations such as uploads, tags, and
metadata.

The cost of that split is that authorization has to happen *before* Caddy hands the file
over, which is what `forward_auth` is for:

1. Client sends `GET /artifacts/demo/greeting/latest` with `Authorization: Bearer ...`.
2. Caddy strips any inbound `X-Magpie-User` / `X-Magpie-Scope` headers -- clients must not be
   able to forge an identity.
3. Caddy makes a subrequest to FastAPI's `/api/v1/auth/validate` with the client's headers.
   FastAPI answers `200` (plus `X-Magpie-User` and `X-Magpie-Scope`) or `401`.
4. On `200`, Caddy serves the file from `/data` with `Content-Disposition: attachment`. On
   `401`, the request is refused and the file is never opened.

Writes and admin operations (`/api/v1/upload/*`, tag mutations, `PATCH` on an artifact,
`/api/v1/tokens*`, `/api/v1/gc`, `/api/v1/status`) go through the same gate and are then
proxied to FastAPI, which enforces the *scope* (`read` / `write` / `admin`) itself. Caddy only
answers "is this a real token?"; FastAPI answers "may it do this?".

Not every route needs a bearer token:

- `GET /health` is public and proxies to FastAPI. The image health check calls it through
  Caddy, so it checks that both services respond.
- Artifact API reads (`GET /api/v1/artifacts*`) and protected file downloads accept requests
  from `MAGPIE_ALLOWED_CIDRS` without a token. For API reads, Caddy passes a synthetic
  read-only identity to FastAPI. Writes and admin operations still need a token. Behind a
  reverse proxy, configure [trusted proxies](installation.md#trusted-proxies) so Caddy checks
  the client's address rather than the proxy's.
- `/artifacts/public/*` is served with **no authentication at all**, and Caddy enables `browse`
  for it, so anonymous callers can *enumerate* the namespace (directory index listing `blobs/`,
  `metadata/`, tag symlinks) as well as fetch known paths. Anything pushed under `public/` is
  world-readable to whoever can reach the port. Treat that namespace as a deliberate publishing
  mechanism, not a default.

## Why TLS is not Magpie's job

Pre-v0.2.0, Magpie ran Caddy and FastAPI as two containers and Caddy terminated TLS itself
(ACME, `MAGPIE_DOMAIN`, `TLS_MODE`). That is gone. The bundled container serves plain HTTP on
`:8080` -- an unprivileged port, so both processes drop root -- and expects something in front
of it to terminate TLS.

Consequences worth knowing:

- The internal Caddy sends no HSTS header, because it cannot tell whether the connection in
  front of it was HTTPS. Your proxy should add one.
- Publish the port on a private interface (`MAGPIE_BIND_IP=127.0.0.1`) when the proxy is on the
  same host; anything that reaches `:8080` directly is talking to Magpie in cleartext.
- Set `MAGPIE_TRUSTED_PROXIES` if you use the CIDR allow-list, so `X-Forwarded-For` is honored
  from your hop -- and *only* your hop.
- `MAGPIE_HTTPS_PORT`, `MAGPIE_DOMAIN`, `TLS_MODE`, and `ACME_SERVER` no longer do anything;
  `magpie-deploy.sh update` strips them on upgrade.

## Dev vs production

In a repo checkout, bare `docker compose up` merges the checked-in **development** override.
Specifying `-f docker-compose.yml` disables that automatic merge. The installer never uses
the checkout's override, but it does load a separate
[site override](installation.md#site-specific-compose-settings) from the install directory.

| | Local development | Release Compose | Installer-managed |
|---|---|---|---|
| Compose files | Repo base file + development override | Digest-pinned release asset | Installed base file + optional site override |
| Config | Shell or local `.env` | `.env` next to the release file | `<install dir>/etc/.env` |
| Image | Built from the checkout | Release image pinned by digest | Selected release image tag |
| First-boot token sink | Defaults to `file` | Set explicitly before first boot | Installer defaults to `file` |
| Lifecycle | `docker compose up -d --build` / `down` | `docker compose up -d` / `down` | `magpie.service` and `magpie-deploy.sh update` |

With the file sink, the token file is owned by the runtime UID and written with mode
`0600`. Use the [installation guide](installation.md) to match the CLI to the server
release and put production instances behind a TLS proxy.

The canonical file drops all capabilities and adds only `SETUID`, `SETGID`, `CHOWN`, and
`DAC_OVERRIDE` for the wrapper's root startup and ownership fixups; the wrapper then runs the
supervised processes as the configured non-root uid. The root filesystem is read-only, with
writable tmpfs mounts for `/run`, `/tmp`, and `/var/lib/caddy`. Development does not relax this
hardening.

## Where state lives

| State | Location | Notes |
|-------|----------|-------|
| Artifact bytes | `/data/artifacts/` blobs, addressed by SHA-256 | The source of truth. Plain files -- `tar` them, `rsync` them, read them without Magpie running. |
| Tags | symlinks alongside the blobs | Moving a tag is a symlink swap; content is immutable. |
| Tokens | `/data/magpie.db` (SQLite) | Hashed tokens and their scopes; no artifact data. |
| First-boot admin token | `/data/admin-token` with `sink=file` | Written `0600` when the database is first initialized; re-issued by `magpie-ctl init --reset-admin-token`. |

The [startup wrapper](../docker/bundled/wrapper.sh) initializes a missing database and
runs migrations before starting FastAPI. It starts Caddy only after FastAPI answers
`/health`. A failed migration stops startup; see [pre-migration database copies](backup-restore.md#pre-migration-database-copies).

Losing `magpie.db` costs you your tokens, not your artifacts: re-run
`magpie-ctl init --reset-admin-token` and re-issue. Losing `/data/artifacts` is the real
disaster -- see [Backup & Restore](backup-restore.md).

## See also

- [Installation Guide](installation.md) -- server vs client install, admin tokens, upgrades
- [Configuration Reference](configuration.md) -- every environment variable
- [Production Checklist](production-checklist.md) -- what to verify before going live
- [API Compatibility](api-compatibility.md) -- client/server version coupling

---

*(AI-generated via Devin; refreshed for v0.2.0 final and audited against v0.2.1 via Devin)*
