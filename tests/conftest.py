"""Shared pytest fixtures for Magpie tests."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from magpie.config import MagpieSettings


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
