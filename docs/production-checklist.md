# Production Checklist

Before deploying to production:

## Pre-Deployment
- [ ] DNS configured for `MAGPIE_DOMAIN`
- [ ] Firewall allows ports 80 and 443
- [ ] Persistent storage provisioned for `MAGPIE_DATA_DIR`
- [ ] Backup destination configured (see [backup-restore.md](backup-restore.md))

## Security Setup
- [ ] `MAGPIE_DEBUG=false`
- [ ] Admin token stored securely (from startup logs)
- [ ] HTTPS enabled (automatic via Let's Encrypt)

## Deploy
```bash
export MAGPIE_DOMAIN=magpie.example.com
export MAGPIE_DATA_DIR=/path/to/persistent/storage
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs magpie | grep "ADMIN TOKEN"
```

## Validation
- [ ] Health check: `curl https://$MAGPIE_DOMAIN/health`
- [ ] Upload test: `magpie push test.txt --to test/artifact`
- [ ] Download test: `magpie get test/artifact:latest`
- [ ] HTTPS redirect works: `curl http://$MAGPIE_DOMAIN` (redirects to https)

## Operations
- [ ] Monitoring configured (setup `/health` endpoint checks)
- [ ] Backup script scheduled (see [backup-restore.md](backup-restore.md))
- [ ] GC schedule planned
- [ ] Token rotation procedure documented
- [ ] Admin token recovery procedure tested

**Recover admin token if lost:**
```bash
docker compose -f docker-compose.prod.yml exec magpie magpie-ctl init --reset-admin-token
```

---

*(AI-generated via Claude Code w/ Sonnet 4.5)*
