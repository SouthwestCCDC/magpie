# Architecture

How a Magpie deployment is put together, and why. For how the storage model behaves
day to day (content addressing, tags, garbage collection), see the
[User Guide](user-guide.md).

## The short version

Since v0.2.0 Magpie ships as **one container** running **two processes**, over **one directory
of files**:

```
  magpie CLI / curl / CI
        |
        v  HTTPS
  your reverse proxy (nginx, Caddy, cloud LB)  --  terminates TLS
        |
        v  HTTP :8080
  +--------------------------- magpie container ---------------------------+
  |  Caddy  --  auth gate; serves /artifacts/* directly from disk          |
  |    |  forward_auth / reverse_proxy                                     |
  |    v                                                                   |
  |  FastAPI (uvicorn, 127.0.0.1:8000)  --  uploads, tags, metadata, auth  |
  +------------------------------------------------------------------------+
        |
        v  bind mount
  /data:  artifacts/  (content-addressed blobs + tag symlinks)
          magpie.db   (SQLite -- tokens only)
```

| Piece | Where it lives | Responsibility |
|-------|----------------|----------------|
| Caddy | inside the image, config baked in (`docker/bundled/Caddyfile`) | Listens on `:8080`; authenticates every request; **serves artifact downloads itself** off `/data`; proxies everything else to FastAPI |
| FastAPI / uvicorn | inside the image, bound to `127.0.0.1:8000` | Uploads, hashing and dedupe, tag mutations, metadata, token management, GC, and the auth decisions Caddy defers to |
| `/data` | host bind mount (`MAGPIE_DATA_DIR`) | `artifacts/` (blobs + tag symlinks), `magpie.db` (tokens), `admin-token` (with the file sink) |
| Your reverse proxy | outside Magpie entirely | TLS, HSTS, hostnames, and any org-wide access control |

## Why Caddy serves the files

Artifact downloads never touch Python. Caddy's `file_server` reads the blob off disk and
streams it with the kernel doing the copying, so a multi-gigabyte pull costs Magpie disk I/O
and nothing else -- no uvicorn worker held open, no Python-level buffering, no memory
proportional to artifact size. FastAPI stays free for the small, stateful requests (upload,
tag, metadata) where application logic is actually required.

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

Two paths behave specially:

- `GET /api/v1/artifacts*` and `GET /artifacts/*` accept an **IP allow-list bypass**: a client
  inside `MAGPIE_ALLOWED_CIDRS` is treated as user `cidr-bypass` with `read` scope and needs no
  token. Writes never bypass. Because the container sits behind a proxy, this only works when
  `MAGPIE_TRUSTED_PROXIES` names the upstream hop -- otherwise the client IP Caddy sees is the
  proxy's and every anonymous read 401s. (In v0.2.0 the allow-list accepts a single range;
  see [issue #612](https://github.com/SouthwestCCDC/magpie/issues/612).)
- `/artifacts/public/*` is served with **no authentication at all**. Anything pushed under the
  `public/` namespace is world-readable to whoever can reach the port. Treat that namespace as
  a deliberate publishing mechanism, not a default.

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

Both use the same `docker-compose.yml`. The difference is whether
`docker-compose.override.yml` gets merged -- bare `docker compose up` merges it automatically;
`docker compose -f docker-compose.yml up -d` does not, and neither does the installer.

| | Local development | Production |
|---|---|---|
| Command | `just up` / `docker compose up -d --build` | `sudo ./scripts/magpie-deploy.sh install` (or `docker compose -f docker-compose.yml up -d`) |
| Compose files | `docker-compose.yml` **+** `docker-compose.override.yml` | `docker-compose.yml` only |
| Image | built locally from `Dockerfile.bundled` | pinned release `ghcr.io/southwestccdc/magpie:X.Y.Z-bundled` via `MAGPIE_IMAGE` |
| `MAGPIE_ADMIN_TOKEN_SINK` | defaults to `file` (overlay convenience) | **must be set explicitly**; container refuses to boot otherwise |
| Admin token location | `./data/admin-token` (root-owned, `0600`) | `<install dir>/data/admin-token`, or your secret store via `sink=exec` |
| Config source | shell environment | `<install dir>/etc/.env` |
| TLS | none -- `http://localhost:8080` | external reverse proxy terminates TLS; publish `:8080` privately |
| Test endpoints | `/api/v1/_test/*` available if `MAGPIE_ENABLE_TEST_ENDPOINTS=true` | never enabled |
| Lifecycle | `docker compose down`; delete `./data` to reset to first boot | systemd unit, backups, `magpie-deploy.sh update` |
| Client | `uv tool install git+https://github.com/SouthwestCCDC/magpie` (tracks `default`) | install the CLI at the **same minor version as the server** -- an older client gets `426 Upgrade Required` |

Everything the canonical file hardens stays hardened in development too: the overlay only adds
a build context, the file-sink default, and the test-endpoint toggle. It does not relax
`read_only`, the dropped capabilities, or the fail-closed environment.

## Where state lives

| State | Location | Notes |
|-------|----------|-------|
| Artifact bytes | `/data/artifacts/` blobs, addressed by SHA-256 | The source of truth. Plain files -- `tar` them, `rsync` them, read them without Magpie running. |
| Tags | symlinks alongside the blobs | Moving a tag is a symlink swap; content is immutable. |
| Tokens | `/data/magpie.db` (SQLite) | Hashed tokens and their scopes. **Only** thing in the database. |
| First-boot admin token | `/data/admin-token` with `sink=file` | Written `0600` on first boot with an empty data directory; re-issued by `magpie-ctl init --reset-admin-token`. |

Losing `magpie.db` costs you your tokens, not your artifacts: re-run
`magpie-ctl init --reset-admin-token` and re-issue. Losing `/data/artifacts` is the real
disaster -- see [Backup & Restore](backup-restore.md).

## See also

- [Installation Guide](installation.md) -- server vs client install, admin tokens, upgrades
- [Configuration Reference](configuration.md) -- every environment variable
- [Production Checklist](production-checklist.md) -- what to verify before going live
- [API Compatibility](api-compatibility.md) -- client/server version coupling

---

*(AI-generated via Devin)*
