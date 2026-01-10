# Magpie

Content-addressed artifact storage with mutable tags.

## Development

```bash
# Install dependencies
uv sync

# Run server locally
uv run uvicorn magpie.server.app:app --reload

# Run CLI
uv run magpie --help
uv run magpie-ctl --help

# Run with Docker Compose (Caddy + Magpie)
docker compose up --build
```

## Project Structure

```
src/magpie/
  server/     # FastAPI application
  storage/    # Filesystem operations (blobs, manifests, tags)
  cli/        # Client CLI (magpie)
  ctl/        # Server admin CLI (magpie-ctl)
```
