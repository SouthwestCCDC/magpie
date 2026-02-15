# Magpie

Content-addressed artifact storage with mutable tags for distributing build artifacts, container images, and deployment packages.

## Why Magpie

Magpie is a lightweight, open source, content-addressed versioned artifact store. If you don't need the overhead of Artifactory or a container registry and just need to store and tag arbitrary files, that's what it's for.

- SHA-256 content addressing with automatic deduplication
- Mutable tags (`latest`, `stable`) pointing to immutable artifacts
- Optional provenance tracking via source URI metadata
- Bearer token auth with optional Authentik SSO
- Garbage collection with configurable retention

## Documentation

- [Quick Start](docs/quickstart.md)
- [Installation Guide](docs/installation.md)
- [User Guide](docs/user-guide.md)
- [Documentation Index](docs/index.md)

## Development

```bash
uv sync                                          # Install dependencies
uv run uvicorn magpie.server.app:app --reload   # Run server locally
docker compose up --build                        # Full stack with Caddy
uv run pytest tests/unit/ -v                     # Run tests
uv run ruff check src/ tests/                    # Lint
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for full development workflow.

## Installation

```bash
# Server (via container)
docker pull ghcr.io/southwestccdc/magpie:latest

# Client (via pip)
uv pip install git+https://github.com/SouthwestCCDC/magpie@v0.1.2
```

Pin to full tags (`1.0.1`) for production; `latest` tracks newest releases.

See [Installation Guide](docs/installation.md) for deployment details and [Release Notes](docs/releases.md) for version history.

---
*Documentation improved with AI assistance (Claude Code w/ Sonnet 4.5).*
