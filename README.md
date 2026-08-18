# Magpie

Content-addressed artifact storage with mutable tags for distributing build artifacts, container images, and deployment packages.

## Why Magpie?

Magpie is a lightweight, open source, content-addressed versioned artifact store. If you don't need the overhead of Artifactory or a container registry and just need to store, tag, and hand out arbitrary files, that's what it's for.

Use Magpie when you need:

- **Simple artifact storage** over plain HTTP -- `push`/`get` a file, no registry API to learn
- **Content deduplication** -- SHA-256 addressing; upload the same bytes twice, store them once
- **Mutable tags over immutable content** -- pin production to `stable` while `latest` moves
- **Automation-friendly access** -- bearer tokens with `read`/`write`/`admin` scopes, plus
  optional IP allow-listing so trusted networks can pull without a token
- **Provenance** -- record where an artifact came from (`--source-uri`) and read it back with `magpie info`
- **Small operational surface** -- one container, a directory of files, and a SQLite database of tokens;
  artifacts stay plain files on disk, so recovery never depends on Magpie itself

**When *not* to use Magpie:**

| You want to... | Use instead |
|----------------|-------------|
| Serve OCI/Docker images to a container runtime | Harbor, Docker Registry, or GHCR |
| Resolve language packages (`pip`, `npm`, Maven) | Artifactory, Nexus, or a language-native index |
| Store objects at scale with lifecycle policies and multi-region durability | S3 or MinIO directly |
| Sync trees of files between hosts | `rsync` |
| Enforce per-path or per-team access control | Something with real RBAC -- Magpie's token scopes are server-global |

Magpie is a good fit when you would otherwise wire up "a directory on a web server, plus a
naming convention" -- it makes that setup content-addressed, tagged, authenticated, and scriptable.

## 5-Minute Quick Start

This gets you from `git clone` to a working `push`/`get` against a **local, throwaway**
server. For a real deployment, use the [installer](docs/quickstart.md) instead.

**Prerequisites:** git, Docker with Compose v2, and [`uv`](https://docs.astral.sh/uv/).
You do *not* need Python 3.13 installed -- `uv` fetches it for the client.

### 1. Start the server

```bash
git clone https://github.com/SouthwestCCDC/magpie
cd magpie
docker compose up -d --build          # builds the bundled image from source
```

`docker-compose.override.yml` is merged automatically here: it builds the image from
`Dockerfile.bundled` and supplies the development default `MAGPIE_ADMIN_TOKEN_SINK=file`.
Compose still prints `warning: The "MAGPIE_ADMIN_TOKEN_SINK" variable is not set` while
interpolating the canonical file -- harmless locally, because the overlay sets it. A real
deployment must set it explicitly; the canonical compose file has no default on purpose.

The stack is a single container serving plain HTTP on <http://localhost:8080>. Wait for it
to report healthy (roughly 10-30 seconds on first boot), then check it:

```bash
docker compose ps                     # STATUS should say "(healthy)"
curl http://localhost:8080/health     # {"status":"ok","version":"..."}
```

### 2. Get your admin token

On first boot Magpie mints a break-glass admin token and delivers it through the sink you
chose above. With `MAGPIE_ADMIN_TOKEN_SINK=file` it is written to a `0600` file in the data
directory -- **not** printed to the logs:

```bash
sudo cat data/admin-token
```

Lost it, or used a different sink? See [Admin token retrieval](#admin-token-retrieval) below.

### 3. Install the client

```bash
uv tool install git+https://github.com/SouthwestCCDC/magpie
```

That installs the `magpie` (client) and `magpie-ctl` (server admin) commands into
`~/.local/bin`. Point the client at the local server:

```bash
export MAGPIE_SERVER=http://localhost:8080
export MAGPIE_TOKEN=$(sudo cat data/admin-token)
magpie status
```

### 4. Push and get an artifact

```bash
echo "Hello Magpie" > hello.txt
magpie push hello.txt --to demo/greeting
magpie ls demo/greeting
magpie get demo/greeting:latest -o roundtrip.txt
cat roundtrip.txt                       # Hello Magpie
```

`magpie get` without `-o` writes a file named after the artifact (`greeting`), not after the
file you uploaded -- content is addressed by hash, and the original filename is not stored.

Tear it down when you're done. The data directory is a host bind mount, so it survives
`down` -- delete it explicitly to start over from a fresh first boot (new admin token):

```bash
docker compose down
sudo rm -rf data
```

Next: [Quick Start](docs/quickstart.md) for a real install, [User Guide](docs/user-guide.md)
for the full CLI, [Configuration Reference](docs/configuration.md) for every environment variable.

### Admin token retrieval

The admin token is generated **only on first boot** (an empty data directory) and delivered
once, through `MAGPIE_ADMIN_TOKEN_SINK`:

| Situation | What to do |
|-----------|------------|
| `sink=file` (the default the installer sets) | `sudo cat <data dir>/admin-token` |
| `sink=stdout` (opt-in, dev only) | `docker compose logs magpie \| grep "ADMIN TOKEN"` |
| Token lost, or `sink=discard` | Mint a new one: `docker compose exec magpie magpie-ctl token create --name ops-admin --scope admin` |
| Want to invalidate and re-issue the break-glass token | `docker compose exec magpie magpie-ctl init --reset-admin-token` (the new token goes through the same sink, e.g. back to the file) |

Grepping the logs only works with `sink=stdout`; every other sink deliberately keeps the
token out of the log stream. See [Admin Token
Delivery](docs/installation.md#admin-token-delivery) for the full matrix.

## Architecture

One container, two processes, one directory of files:

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

- **Caddy** (inside the image, not operator-facing) is the front door on `:8080`. It gates every
  request against FastAPI's `/api/v1/auth/validate` (or an `MAGPIE_ALLOWED_CIDRS` match), then
  either proxies API calls to FastAPI or serves artifact downloads *itself*, straight off disk.
  Downloads never pass through Python, so a 5 GB pull costs Magpie nothing but disk I/O.
- **FastAPI** (uvicorn on container-loopback `:8000`) handles uploads, hashing/dedupe, tag
  mutations, metadata, token management, GC, and auth decisions.
- **The filesystem is the source of truth.** Artifacts are content-addressed blobs under
  `/data/artifacts` with tags as symlinks; **SQLite (`/data/magpie.db`) stores tokens only.**
- **TLS is not Magpie's job.** The container speaks plain HTTP on `:8080` and expects an
  external reverse proxy (nginx, another Caddy, a cloud load balancer) in front of it.

See [Architecture](docs/architecture.md) for the request-path details and a dev-vs-production
comparison.

## Installation

```bash
# Server: use the installer (resolves and pins the right image tag, sets up systemd)
sudo ./scripts/magpie-deploy.sh install --noninteractive

# Client: standalone CLI install (brings its own Python 3.13)
uv tool install git+https://github.com/SouthwestCCDC/magpie
```

Pin production servers to a released bundled image (`ghcr.io/southwestccdc/magpie:X.Y.Z-bundled`);
`latest` is not a safe target for the v0.2.0 compose topology. See the [Installation
Guide](docs/installation.md) for server-vs-client details and [Release
Notes](docs/releases.md) for version history.

## Documentation

- [Quick Start](docs/quickstart.md) -- deploy a real server with the installer
- [Installation Guide](docs/installation.md) -- server and client installation, admin tokens, upgrades
- [Configuration Reference](docs/configuration.md) -- every environment variable, and which ones you actually need
- [Architecture](docs/architecture.md) -- components, request path, dev vs production
- [User Guide](docs/user-guide.md) -- full CLI reference
- [Documentation Index](docs/index.md)

## Development

```bash
just sync                                        # Install dependencies
just serve                                       # Run the API locally with auto-reload
just up                                          # Full bundled stack via Docker Compose
just test-unit                                   # Unit tests
just check                                       # Lint + format check + tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full development workflow.

---
*Documentation improved with AI assistance (Claude Code w/ Sonnet 4.5; first-user-experience
overhaul via Devin).*
