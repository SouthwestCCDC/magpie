# Magpie Assessment Findings

Assessment conducted: 2026-01-11
Re-validated: 2026-01-19 (doc cleanup)

## Status Legend
- [ ] Not started
- [~] In progress / partially resolved
- [x] Resolved

---

## 1. Upload Endpoint Missing Scope Validation

**Status**: [x] RESOLVED (verified 2026-01-12)
**Priority**: Low (Security - defense in depth)
**Location**: `src/magpie/server/routes/upload.py`
**Issue**: #68

~~The upload endpoint accepts an `X-Magpie-Scope` header from Caddy but doesn't validate it server-side.~~

**Resolution**: `require_write_scope` dependency now exists in `deps.py` (lines 68-89) and is used by the upload endpoint (line 34). All write endpoints now have proper scope validation. Issue #68 can be closed.

---

## 2. GC Logic Duplicated Across Three Modules

**Status**: [x] RESOLVED - issues #69, #71 both closed
**Priority**: High (Maintainability + Performance)
**Issues**: #69 (duplication), #71 (blocking event loop)

**Resolution**: Both issues have been closed. GC logic is now consolidated.

---

## 3. CLI Error Handler Duplicated in 10 Files

**Status**: [x] RESOLVED - issue #70 closed
**Priority**: High (Maintainability)
**Issue**: #70

**Resolution**: Issue #70 has been closed. CLI error handling consolidated.

---

## 4. Design Docs Don't Match Implementation

**Status**: [x] RESOLVED - issue #27 closed
**Priority**: Medium (Documentation)
**Related issue**: #27

**Resolution**: Issue #27 has been closed. Design docs updated to match implementation.

---

## 5. Missing Operational Documentation

**Status**: [x] RESOLVED - #7 closed, #8 closed
**Priority**: Low (Documentation - expected for new codebase)
**Related issues**: #7 (closed), #8 (closed)

- Issue #7 (Manual Acceptance Testing Plan): CLOSED
- Issue #8 (Installation and Admin Guides): CLOSED (PR #157 - installer script)

User guide exists and is excellent. PR #157 added `scripts/install.sh` with install, update, uninstall, status, and logs commands for Debian 12/13 deployment.

---

## 6. Minor Code Quality Issues

**Status**: [ ] Not started
**Priority**: Low
**Issue**: #164

| Issue | Location | Notes |
|-------|----------|-------|
| Eager DB initialization | `auth/service.py:66` | TokenService inits DB even if unused |
| Broad exception catch | `server/routes/gc.py:145` | Bare `except Exception` |
| Missing type hint | GC helper `artifact_dir` param | Minor typing gap |

---

## 7. User Guide Documentation Gaps

**Status**: [x] RESOLVED - PR #147 merged
**Priority**: Medium (Documentation)

**Resolution**: PR #147 addressed all documentation gaps:
- Added `magpie flush-tag` command documentation
- Added `magpie config` command documentation
- Documented `magpie get --force` option
- Documented `magpie-ctl gc` options (--reconcile-only, --retention-days, --quiet, --json-output)
- Documented `magpie-ctl init --reset-admin-token`
- Added `POST /api/v1/tags/{tag_name}/flush` API endpoint

---

## 8. Design Docs Pending Features

**Status**: [x] RESOLVED - all implemented or closed
**Priority**: Low (Future work)
**Updated**: 2026-01-19

Features from design docs - status:
- S3 backup sync - **#175** CLOSED
- Authentik SSO integration - **#176** CLOSED
- Structured logging with structlog - **#167** CLOSED
- OpenTelemetry tracing - **#168** CLOSED
- Sentry error tracking - **#169** CLOSED
- Upload size limits - **#103** CLOSED (`MAGPIE_MAX_UPLOAD_SIZE`)

---

## 9. Test Coverage Status

**Status**: [x] Excellent
**Verified**: 2026-01-13

- **997+ test cases** across 63 test files (up from 756/53 on 2026-01-12)
- Includes unit, integration, security, concurrency, and E2E tests

Test pyramid is well-balanced with good error path coverage.

---

## Discussion Notes

(Will be updated as we talk through these)

