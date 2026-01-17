#!/usr/bin/env python3
"""Manual test script to demonstrate structured logging output.

This script simulates the logging behavior without starting the full server.
Run with:
    python3 scripts/test_structured_logging.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import structlog

from magpie.config import MagpieSettings
from magpie.logging_config import configure_logging


def test_json_logging():
    """Test JSON format logging."""
    print("=" * 60)
    print("Testing JSON format logging (MAGPIE_LOG_FORMAT=json)")
    print("=" * 60)

    settings = MagpieSettings(log_format="json", debug=False)
    configure_logging(settings)

    logger = structlog.get_logger("magpie.test")

    # Simulate various log types
    logger.info(
        "upload_complete",
        artifact_path="images/infra/vrouter.qcow2",
        hash="abc12345def67890",
        hash_ref="@abc12345",
        size_bytes=1073741824,
        duration_ms=4523.45,
        uploaded_by="admin",
        is_duplicate=False,
    )

    logger.info(
        "tag_created",
        operation="create_tag",
        tag_name="stable",
        artifact_path="images/infra/vrouter.qcow2",
        hash_ref="@abc12345",
    )

    logger.info(
        "gc_complete",
        dry_run=False,
        reconcile_only=False,
        artifacts_scanned=42,
        blobs_found=156,
        blobs_deleted=8,
        space_reclaimed_bytes=524288000,
        symlinks_checked=42,
        symlinks_fixed=2,
        items_removed=3,
    )

    logger.warning(
        "auth_validation_failed",
        reason="invalid_token",
    )

    logger.info(
        "auth_validation_success",
        token_name="deployment-bot",
        scope="write",
    )

    print()


def test_console_logging():
    """Test console format logging."""
    print("=" * 60)
    print("Testing console format logging (MAGPIE_LOG_FORMAT=console)")
    print("=" * 60)

    settings = MagpieSettings(log_format="console", debug=True)
    configure_logging(settings)

    logger = structlog.get_logger("magpie.test")

    # Simulate various log types
    logger.info(
        "upload_complete",
        artifact_path="images/infra/vrouter.qcow2",
        hash="abc12345def67890",
        size_bytes=1073741824,
        duration_ms=4523.45,
    )

    logger.info(
        "tag_created",
        operation="create_tag",
        tag_name="stable",
        artifact_path="images/infra/vrouter.qcow2",
    )

    logger.debug(
        "gc_processing_artifact",
        artifact_path="images/infra/vrouter.qcow2",
    )

    print()


def test_context_vars():
    """Test context variables (request_id)."""
    print("=" * 60)
    print("Testing context variables (request correlation)")
    print("=" * 60)

    settings = MagpieSettings(log_format="json", debug=False)
    configure_logging(settings)

    # Simulate request middleware binding context
    structlog.contextvars.bind_contextvars(request_id="req_abc123xyz")

    logger = structlog.get_logger("magpie.server.routes.upload")

    logger.info("request_start", method="POST", path="/api/v1/upload/test/file.txt")

    logger.info(
        "upload_complete",
        artifact_path="test/file.txt",
        hash="def67890",
        duration_ms=123.45,
    )

    logger.info("request_complete", method="POST", status_code=200, duration_ms=125.67)

    # Clear context
    structlog.contextvars.clear_contextvars()

    print()


def main():
    """Run all test scenarios."""
    print("\n" + "=" * 60)
    print("STRUCTURED LOGGING DEMONSTRATION")
    print("=" * 60)
    print()

    test_json_logging()
    test_console_logging()
    test_context_vars()

    print("=" * 60)
    print("DEMONSTRATION COMPLETE")
    print("=" * 60)
    print()
    print("Key features demonstrated:")
    print("- JSON and console output formats")
    print("- Structured fields (artifact_path, hash, duration_ms, etc.)")
    print("- Request correlation via request_id context")
    print("- Operation-specific events (upload_complete, gc_complete, etc.)")
    print()


if __name__ == "__main__":
    main()
