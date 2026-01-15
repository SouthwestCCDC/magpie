# Magpie Project - Proposed Issues Analysis

Analysis date: 2026-01-12
Based on: design docs, assessment findings, comprehensive review, and 104 existing issues (24 open, 80 closed)

## Summary

14 proposed new issues across 6 categories, plus 5 open questions needing human decision.

---

## PROPOSED ISSUES

### Category 1: Phase 8 Deployment Follow-up (P1)

**Issue A: Deploy magpie to OpenNebula VM for testing**
- Category: Deployment/Infrastructure
- Description: Create an OpenNebula VM with persistent volumes to test magpie deployment before bootstrap integration. Bridges issues #106-108 and provides staging for Phase 8 acceptance testing.
- Acceptance Criteria:
  - VM created with 1.8TB volume
  - docker-compose deployed and auto-init works
  - Ansible playbook from #106 tested on this VM
  - magpie status command (#107) verified working
  - .env.example (#108) validated
- Depends On: #99, #100, #101, #102, #103, #104, #105 (Phase 7 security fixes)
- Complexity: M

**Issue B: Integration test magpie with OpenNebula image provider**
- Category: Integration/Infrastructure
- Description: Test ONE integration - verify OpenNebula can use magpie for VM images. Start with manual ONE CLI calls, then document Terraform integration path.
- Acceptance Criteria:
  - Document ONE API calls needed to register magpie as image provider
  - Manual test: Deploy ONE image from magpie
  - Identify Terraform changes needed (no implementation yet)
- Depends On: #106, VM from Issue A
- Complexity: M

**Issue C: Performance testing - large file downloads over network**
- Category: Testing/Performance
- Description: Test magpie performance with realistic conditions (large qcow2 downloads, concurrent users). Validate Caddy direct serving meets requirements.
- Acceptance Criteria:
  - Test 1GB+ file downloads from VM to workstation
  - Measure bandwidth/latency
  - Compare Caddy direct vs API-proxied downloads
  - Document acceptable thresholds
- Depends On: VM from Issue A
- Complexity: M

---

### Category 2: Observability & Monitoring (P2)

**Issue D: Implement structured logging with structlog**
- Category: Observability
- Description: Design specifies structlog for structured JSON logging (design.md line 254), but implementation uses Python's standard logging.
- Acceptance Criteria:
  - Replace logging.getLogger() with structlog.get_logger()
  - All key operations emit structured logs (upload, tag, GC, auth)
  - Verify JSON output includes trace correlation IDs
  - Add MAGPIE_LOG_FORMAT config option
- Complexity: M

**Issue E: Configure OpenTelemetry tracing**
- Category: Observability
- Description: Design specifies OpenTelemetry support (design.md line 256). Implement OTEL middleware in FastAPI.
- Acceptance Criteria:
  - OTEL middleware added to FastAPI app
  - Traces exported to configurable endpoint (Jaeger, OTLP)
  - MAGPIE_OTEL_ENABLED config option (stub exists)
  - Test: Trace complete request (upload -> tag -> GC)
- Complexity: M

**Issue F: Configure Sentry error tracking**
- Category: Observability
- Description: Design specifies Sentry for error tracking and APM (design.md line 255).
- Acceptance Criteria:
  - Sentry SDK integrated with FastAPI
  - MAGPIE_SENTRY_DSN config option populated (stub exists)
  - Errors from upload, tag, GC endpoints reported
  - Test: Trigger error and verify appears in Sentry
- Complexity: S

---

### Category 3: Future Features (P3 - Deferred)

**Issue G: Implement S3 backup sync (Phase 4)**
- Category: Feature/Backup
- Description: Design mentions S3 sync as "should have" feature. Implement `magpie-ctl sync --to-s3`.
- Acceptance Criteria:
  - `magpie-ctl sync --to-s3` command implemented
  - Syncs only tagged artifacts (respects retention)
  - Handles incremental sync (don't re-upload existing)
  - Update admin guide with backup procedure
- Complexity: L

**Issue H: Authentik SSO integration**
- Category: Feature/Auth
- Description: Design mentions "SSO via Authentik" for human browser access (design.md line 191).
- Acceptance Criteria:
  - Caddy configured for Authentik forward_auth
  - Humans can browse `/artifacts/` without bearer token
  - Admin UI shows logged-in user
  - Document Authentik configuration
- Complexity: M

---

### Category 4: Documentation & Guides (P2)

**Issue I: Create comprehensive backup and restore guide**
- Category: Documentation/Operations
- Description: Admin guide exists (issue #8) but lacks detailed backup/restore procedures.
- Acceptance Criteria:
  - Document what to back up (storage dir, SQLite DB, manifests)
  - Restore from backup procedure (with test)
  - Automate backup scheduling (cron/systemd timer)
  - Recovery scenarios (corrupted manifest, lost DB)
- Complexity: M

**Issue J: Create troubleshooting guide**
- Category: Documentation/Operations
- Description: Operators need troubleshooting guide for common failure modes.
- Acceptance Criteria:
  - Document common error messages and solutions
  - Symlink reconciliation procedure
  - Manifest corruption recovery
  - Storage quota management
  - Debug mode usage
- Complexity: M

**Issue K: Document legacy compatibility layer**
- Category: Documentation
- Description: Proposal mentions "magpie supports legacy-compatible URLs/auth for transitional period."
- Acceptance Criteria:
  - Document legacy-compatible URL formats
  - Document backward-compatible auth scheme (if any)
  - Test: Verify old clients can migrate to magpie
- Complexity: M
- Notes: Only needed if gradual migration from old server is planned

---

### Category 5: Testing Gaps (P2)

**Issue L: Add integration test for magpie-ctl tool**
- Category: Testing
- Description: The `magpie-ctl` tool (init, gc, sync) lacks comprehensive integration tests.
- Acceptance Criteria:
  - Test `magpie-ctl init` creates correct directory structure
  - Test `magpie-ctl gc` with various retention scenarios
  - Test `magpie-ctl token` operations
  - Integration test with actual filesystem (not mocked)
- Complexity: M

**Issue M: Add docker-compose integration test**
- Category: Testing
- Description: E2E test the full docker-compose stack (Caddy + FastAPI + SQLite) in CI.
- Acceptance Criteria:
  - CI runs docker-compose up in test mode
  - Full user workflow tested (push -> get -> tag -> download)
  - Verifies Caddy routing works correctly
  - Tests network-level edge cases (timeouts, connection resets)
- Complexity: M

---

### Category 6: Operations Tasks (P1 - Future)

**Issue N: Plan and execute legacy artifacts server migration**
- Category: Deployment/Operations
- Description: Coordination issue for migrating existing S3 artifacts and clients from old server to magpie.
- Acceptance Criteria:
  - Document migration strategy (parallel running, cutover date, rollback plan)
  - Identify all consumers (ONE images, Ansible playbooks, manual ops)
  - Document communication plan for game teams
  - Plan testing with backup legacy server
  - Create runbooks for cutover
- Depends On: Phase 7/8 completion
- Complexity: M
- Notes: Coordination only, not implementation

---

## OPEN QUESTIONS (Need Human Decision)

### Q1: S3 Backup Priority
Design lists S3 backup as "should have" but proposes Phase 4 (future). Should this be scheduled for next sprint, or defer until after initial production deployment?

### Q2: Authentik SSO Timing
Design mentions SSO for humans browsing artifacts. Is this required before production, or can it be added post-launch with basic auth/tokens sufficient initially?

### Q3: Observability Infrastructure
Are Sentry and OpenTelemetry instances available in SWCCDC infrastructure? Or should these be optional/pluggable?

### Q4: Legacy Migration Timing
Old artifacts server currently serves ~168GB. Should migration planning happen now (before Phase 8 completion), or defer until magpie is stable in production?

### Q5: OpenNebula Integration Scope
Design mentions manual/Terraform approach for ONE integration (not auto-refresh). Should we create explicit issues for ONE Terraform changes, or keep as ad-hoc during migration?

---

## SUMMARY TABLE

| Category | Count | Priority | Blocked By |
|----------|-------|----------|------------|
| Deployment/Testing (Phase 8 follow-up) | 3 | P1 | Phase 7 security fixes |
| Observability (Design requirement) | 3 | P2 | None |
| Future Features (Deferred) | 2 | P3 | None |
| Documentation (Medium) | 3 | P2 | Existing work |
| Testing Gaps (Medium) | 2 | P2 | None |
| Operations/Migration (Coordination) | 1 | P1 | Phase 7/8 completion |
| **TOTAL PROPOSED** | **14** | | |

---

## ALREADY COVERED (Existing Issues)

- Phase 7 Security: #99-#105
- Phase 8 Initial Deployment: #106-#108
- Documentation: #8, #30, #109-#111
- Technical Debt: #69-#71, #112-#118
- Acceptance Testing: #7, #81
