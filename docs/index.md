# Magpie Documentation

Welcome to the Magpie documentation. Magpie is a content-addressed artifact storage system with mutable tags, designed for distributing build artifacts, container images, and other binary assets across infrastructure.

## Getting Started

New to Magpie? Start here:

1. **[User Guide](user-guide.md)** - Complete guide to using the Magpie CLI and API
   - Installation and setup
   - Core concepts (blobs, tags, hash refs)
   - Authentication with bearer tokens
   - CLI commands (push, get, ls, tag, etc.)
   - Common use cases and examples

2. **[Design Document](design.md)** - Architecture and design rationale
   - Current state and migration from S3-based artifacts
   - Requirements and design decisions
   - Storage architecture and content-addressing
   - Implementation phases and roadmap

## Integration Guides

- **[Ansible Integration](ansible-integration.md)** - Download artifacts from Ansible playbooks
  - Basic download patterns with `get_url`
  - Checksum verification
  - Secure token handling with Ansible Vault
  - Example playbooks for common scenarios

- **[Authentik SSO Setup](authentik-setup.md)** - Configure Authentik SSO for browser access
  - Forward auth provider configuration
  - Application setup in Authentik
  - Caddy configuration for SSO
  - Troubleshooting SSO issues

- **[Authentik Testing Guide](authentik-testing-guide.md)** - Manual testing procedures for SSO integration
  - Test plan overview
  - Bearer token authentication tests
  - Browser-based SSO verification
  - Session persistence testing

## Operations

- **[Backup and Restore Guide](backup-restore.md)** - Backup procedures and disaster recovery
  - What to back up (storage, metadata, database)
  - Backup procedures (manual and automated)
  - Restore procedures for various scenarios
  - Verification and testing backups

## Observability

- **[Structured Logging](structured-logging.md)** - Structured logging implementation with structlog
  - Configuration (JSON vs console format)
  - Standard log fields and request correlation
  - OpenTelemetry integration
  - Log aggregation setup

- **[Sentry Verification Guide](sentry-verification.md)** - Verify Sentry error tracking
  - Setup and configuration
  - Verification tests
  - Troubleshooting integration issues

## Additional Resources

- **[README](../README.md)** - Quick start and project overview
- **[GitHub Repository](https://github.com/SouthwestCCDC/magpie)** - Source code and issue tracker
- **[Release Notes](https://github.com/SouthwestCCDC/magpie/releases)** - Version history and changelog

## Quick Reference

### Common Commands

```bash
# Upload an artifact
magpie push myfile.tar.gz --to images/ubuntu

# Download an artifact
magpie get images/ubuntu:latest

# List versions
magpie ls images/ubuntu

# Create a tag
magpie tag images/ubuntu:latest --as stable

# Run garbage collection
magpie-ctl gc --retention-days 30
```

### Authentication

Magpie supports two authentication methods:

1. **Bearer Tokens** - For CLI, API, and automation (required for uploads and tag management)
2. **Authentik SSO** - For human browser access to artifacts (optional, see [Authentik Setup](authentik-setup.md))

### Environment Variables

```bash
export MAGPIE_SERVER=https://magpie.example.com
export MAGPIE_TOKEN=mgp_your_token_here
```

## Contributing

For information on contributing to Magpie, see the [GitHub repository](https://github.com/SouthwestCCDC/magpie) and check open issues.
