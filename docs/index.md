# Magpie Documentation

## Getting Started

- [Installation Guide](installation.md) - Server deployment and client setup
- [User Guide](user-guide.md) - CLI usage, artifact operations, and API reference

## Deployment

- [Production Readiness Checklist](production-checklist.md) - Pre-deployment verification steps
- [Backup & Restore](backup-restore.md) - Data protection procedures
- [Monitoring](monitoring.md) - Health check endpoints and basic monitoring

## Authentication

- [Authentik SSO Setup](authentik-setup.md) - Configure SSO for browser access
- [Authentik Testing Guide](authentik-testing-guide.md) - Verify SSO integration

## Operations

- [Structured Logging](structured-logging.md) - JSON log configuration and correlation IDs
- [Sentry Verification](sentry-verification.md) - Error tracking integration testing

## Integration

- [Ansible Integration](ansible-integration.md) - Download artifacts in Ansible playbooks

## Suggested Reading Order

New users: Start with [Installation Guide](installation.md) -> [User Guide](user-guide.md) -> [Production Readiness Checklist](production-checklist.md)

Operators: Review [Backup & Restore](backup-restore.md) -> [Monitoring](monitoring.md) -> [Structured Logging](structured-logging.md)

## Design Documentation

Comprehensive design documentation and architecture decisions are maintained in the `deployment` repository at `docs/docs/projects/active/magpie/` rather than in this repository. This follows the SWCCDC documentation consolidation pattern where cross-project design docs live in the central documentation site.

---

*This documentation was generated with AI assistance (Claude Code w/ Sonnet 4.5)*
