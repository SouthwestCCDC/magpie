"""Shared pytest fixtures for Magpie tests."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Generator

import pytest
import structlog

from magpie.config import MagpieSettings


@pytest.fixture(autouse=True)
def _isolate_logging_state() -> Generator[None, None, None]:
    """Snapshot and restore global structlog/stdlib logging config around every test.

    structlog.configure() and the stdlib root logger are process-global, so any test
    that reconfigures them (directly, or via a fixture that calls
    structlog.reset_defaults()) can leak that state into every test that runs
    afterward in the same process. reset_defaults() in particular resets structlog to
    its own *library* defaults -- ConsoleRenderer + PrintLoggerFactory, which writes
    to stdout -- not to the app's production defaults (JSON to stderr, configured
    once at import time by magpie.server.app). Nothing re-applies those production
    defaults afterward, so once a test leaves structlog on library defaults, later
    tests that log through the app's loggers get console-formatted output mixed into
    stdout.

    This fixture is declared here (not in a test module) and is autouse, so pytest
    sets it up before, and tears it down after, any per-module logging fixtures --
    guaranteeing the snapshot taken here is what's restored, regardless of what
    happens inside the test or its other fixtures.
    """
    structlog_config = structlog.get_config()
    root_logger = logging.getLogger()
    root_level = root_logger.level
    root_handlers = root_logger.handlers[:]

    yield

    structlog.configure(**structlog_config)
    root_logger.setLevel(root_level)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    for handler in root_handlers:
        root_logger.addHandler(handler)


@pytest.fixture
def temp_storage_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for test storage.

    Yields the path to the temporary directory and cleans up after the test.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="magpie_test_"))
    try:
        yield tmp_dir
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture
def mock_config(temp_storage_dir: Path) -> MagpieSettings:
    """Return a test MagpieSettings instance with temporary paths.

    Creates the necessary subdirectories for temp storage.
    """
    config = MagpieSettings(storage_path=temp_storage_dir)
    config.storage_path.mkdir(parents=True, exist_ok=True)
    config.temp_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def sample_artifact_bytes() -> bytes:
    """Generate deterministic test content bytes.

    Returns consistent test data for reproducible tests.
    """
    return b"magpie test artifact content v1.0"
