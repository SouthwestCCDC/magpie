# Quick Start Guide

Get Magpie running in under 5 minutes.

## Prerequisites

- Docker and Docker Compose
- `uv` (Python package manager) - [installation guide](https://docs.astral.sh/uv/)

## Local Development Setup

```bash
# Clone the repository
git clone https://github.com/SouthwestCCDC/magpie
cd magpie

# Start the server
docker compose up -d

# Get the admin token from container logs
docker compose logs magpie | grep "ADMIN TOKEN"
```

The admin token format is `mgp_ADMIN_...`. Save it for the next steps.

## Client Setup

```bash
# Install the Magpie client
uv pip install git+https://github.com/SouthwestCCDC/magpie

# Configure connection
export MAGPIE_SERVER=http://localhost:8080
export MAGPIE_TOKEN=mgp_ADMIN_...  # Use the token from logs

# Verify connection
magpie status
```

## First Upload

```bash
# Create a test file
echo "Hello from Magpie!" > hello.txt

# Upload it with a tag
magpie push hello.txt --to demo/greeting

# List what you uploaded
magpie ls demo/greeting

# Download it back
magpie get demo/greeting:latest

# Check the content
cat hello.txt
```

## Creating Additional Tokens

The admin token is powerful - create scoped tokens for everyday use:

```bash
# Create a read-only token
docker compose exec magpie magpie-ctl token create --name readonly --scope read

# Create a read-write token for CI
docker compose exec magpie magpie-ctl token create --name ci-deployer --scope write
```

## Common Workflows

### Version Pinning

```bash
# Push a new version
magpie push app.tar.gz --to apps/myapp

# The upload creates a tag with the current timestamp
# Pin it to a stable tag
magpie tag apps/myapp:2025-02-13T12-34-56 --as stable

# Or use explicit version tags
magpie tag apps/myapp:latest --as v1.2.0
```

### Scripting with URLs

```bash
# Get a direct download URL (no auth required for downloads)
URL=$(magpie url apps/myapp:stable)

# Use it in scripts
curl -O "$URL"
wget "$URL"
```

### Provenance Tracking

```bash
# Tag with source information
magpie push artifact.tar.gz --to builds/myapp \
  --source "https://github.com/org/repo/commit/abc123"

# View the metadata
magpie info builds/myapp:latest
```

## Next Steps

- **Production deployment**: [Installation Guide](installation.md)
- **Complete CLI reference**: [User Guide](user-guide.md)
- **Operations**: [Production Checklist](production-checklist.md)
- **Backup procedures**: [Backup & Restore](backup-restore.md)

## Troubleshooting

### Connection Refused

If `magpie status` fails with "connection refused":

```bash
# Check if the container is running
docker compose ps

# Check logs for errors
docker compose logs magpie
```

### Token Not Working

Ensure the token is correctly set:

```bash
# Show current configuration
magpie config

# Verify MAGPIE_SERVER and MAGPIE_TOKEN are set
```

### Port Already in Use

If port 8080 is already in use:

```bash
# Change the port
export MAGPIE_HTTP_PORT=8888
docker compose up -d

# Update client config
export MAGPIE_SERVER=http://localhost:8888
```

---

*(AI-generated via Claude Code w/ Sonnet 4.5)*
