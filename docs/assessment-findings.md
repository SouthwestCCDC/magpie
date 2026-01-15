# Magpie Assessment Findings

Assessment conducted: 2026-01-11
Re-validated: 2026-01-13 (post PR #147, #150 merge)

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

**Status**: [~] PARTIALLY RESOLVED - #7 closed, #8 still open
**Priority**: Low (Documentation - expected for new codebase)
**Related issues**: #7 (closed), #8 (open)

- Issue #7 (Manual Acceptance Testing Plan): CLOSED
- Issue #8 (Installation and Admin Guides): Still OPEN

User guide exists and is excellent; operational docs for administrators remain a gap.

---

## 6. Minor Code Quality Issues

**Status**: [ ] Not started
**Priority**: Low

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

## 8. Design Docs Pending Features (Deferred)

**Status**: [~] Partially implemented
**Priority**: Low (Future work)

Features in design docs but not implemented:
- S3 backup sync (`magpie-ctl sync --to-s3/--from-s3`) - Phase 4
- Authentik SSO integration - Future work
- Structured logging with structlog - Not prioritized
- Custom OTEL metrics (magpie_uploads_total, etc.) - Not prioritized
- ~~Upload size limits~~ - **IMPLEMENTED** via Issue #103 (`MAGPIE_MAX_UPLOAD_SIZE`)

These are acknowledged deferrals, not bugs.

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

