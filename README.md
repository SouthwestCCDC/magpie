# Magpie

Content-addressed artifact storage with mutable tags for distributing build artifacts, container images, and deployment packages.

- SHA-256 content addressing with automatic deduplication
- Mutable tags (`latest`, `stable`) pointing to immutable artifacts
- Optional provenance tracking via source URI metadata
- Bearer token auth with optional Authentik SSO
- Garbage collection with configurable retention

See [Quick Start Guide](docs/quickstart.md) to get running in 5 minutes.

## Documentation

- [Quick Start](docs/quickstart.md) - Get running in 5 minutes
- [Installation Guide](docs/installation.md) - Server deployment and client setup
- [User Guide](docs/user-guide.md) - Complete CLI and API reference
- [Documentation Index](docs/index.md) - Full documentation overview

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
uv pip install git+https://github.com/SouthwestCCDC/magpie@vX.Y.Z
```

Container tags: Full versions (`1.0.1`) are immutable. Major/minor tags (`1`, `1.0`) and `latest` are mutable and track the latest patch release. Pin to full versions for stable deployments.

See [Installation Guide](docs/installation.md) for deployment details and [Release Notes](docs/releases.md) for version history.

---
*Documentation improved with AI assistance (Claude Code w/ Sonnet 4.5).*
