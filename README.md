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

## Production Deployment

For production with TLS/HTTPS:

```bash
# Set required environment variables
export MAGPIE_DOMAIN=magpie.example.com
export MAGPIE_DATA_DIR=/path/to/persistent/storage

# Start with production configuration
docker compose -f docker-compose.prod.yml up -d
```

The production configuration (`docker-compose.prod.yml` and `Caddyfile.prod`) includes:
- Automatic TLS via Let's Encrypt (or manual certificate configuration)
- Security headers (HSTS, X-Frame-Options, CSP, etc.)
- JSON access logging
- Rate limiting must be configured at infrastructure layer - see [issue #129](https://github.com/SouthwestCCDC/magpie/issues/129) for implementation options (custom Caddy build, FastAPI middleware, or load balancer)

For manual TLS certificates, edit `Caddyfile.prod` and uncomment the `tls` directive
with your certificate paths.

## Project Structure

```
src/magpie/
  server/     # FastAPI application
  storage/    # Filesystem operations (blobs, manifests, tags)
  cli/        # Client CLI (magpie)
  ctl/        # Server admin CLI (magpie-ctl)
```
