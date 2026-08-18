"""Integration tests for the magpie-ctl verify command.

These tests store real artifacts through StorageService (no mocking), then
damage the store on disk and assert the command's report, JSON output, and
exit codes.
"""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path
from typing import Generator

import pytest
from click.testing import CliRunner

from magpie.config import get_settings
from magpie.ctl import cli as ctl_cli
from magpie.storage.paths import blob_path, metadata_path
from magpie.storage.service import StorageService
from tests.integration.test_ctl import EnvCliRunner


@pytest.fixture
def verify_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[CliRunner, Path, StorageService], None, None]:
    """Provide a CLI runner, storage path, and StorageService sharing one store."""
    get_settings.cache_clear()

    storage_path = tmp_path / "storage"
    storage_path.mkdir()

    monkeypatch.setenv("MAGPIE_STORAGE_PATH", str(storage_path))
    monkeypatch.setenv("MAGPIE_DATABASE_PATH", str(tmp_path / "magpie.db"))

    runner = EnvCliRunner(
        env_overrides={
            "MAGPIE_STORAGE_PATH": str(storage_path),
            "MAGPIE_DATABASE_PATH": str(tmp_path / "magpie.db"),
        }
    )
    service = StorageService(get_settings())

    try:
        yield runner, storage_path, service
    finally:
        get_settings.cache_clear()


def store(service: StorageService, artifact_path: str, content: bytes) -> str:
    """Store an artifact and return its full hash."""
    info, _ = service.store_artifact(
        artifact_path=artifact_path,
        file_stream=io.BytesIO(content),
        uploaded_by="test",
    )
    return info.hash


class TestVerifyHealthyStore:
    """Verification of an intact store."""

    def test_empty_storage_reports_clean(
        self, verify_env: tuple[CliRunner, Path, StorageService]
    ) -> None:
        runner, _, _ = verify_env

        result = runner.invoke(ctl_cli, ["verify"])

        assert result.exit_code == 0
        assert "Verify Summary:" in result.output
        assert "Blobs scanned: 0" in result.output

    def test_intact_artifacts_pass(
        self, verify_env: tuple[CliRunner, Path, StorageService]
    ) -> None:
        runner, _, service = verify_env
        store(service, "openvpn/ca", b"-----BEGIN CERTIFICATE-----\n")
        store(service, "images/ubuntu", b"disk image bytes")

        result = runner.invoke(ctl_cli, ["verify"])

        assert result.exit_code == 0
        assert "Blobs scanned: 2" in result.output
        assert "OK: 2" in result.output

    def test_nonexistent_storage_path_errors(
        self, verify_env: tuple[CliRunner, Path, StorageService]
    ) -> None:
        runner, storage_path, _ = verify_env
        shutil.rmtree(storage_path)

        result = runner.invoke(ctl_cli, ["verify"])

        assert result.exit_code == 1
        assert "Storage path does not exist" in result.output


class TestVerifyDetectsDamage:
    """Detection of corrupted, truncated, and missing storage."""

    def test_corrupted_blob_exits_with_integrity_error(
        self, verify_env: tuple[CliRunner, Path, StorageService]
    ) -> None:
        runner, storage_path, service = verify_env
        full_hash = store(service, "openvpn/ca", b"critical key material")
        blob_path(storage_path / "openvpn/ca", full_hash).write_bytes(b"tampered!!!!!!!!!!!!!")

        result = runner.invoke(ctl_cli, ["verify"])

        assert result.exit_code == 5
        assert "MISMATCH: openvpn/ca" in result.output
        assert "Mismatched: 1" in result.output

    def test_truncated_blob_exits_with_integrity_error(
        self, verify_env: tuple[CliRunner, Path, StorageService]
    ) -> None:
        runner, storage_path, service = verify_env
        full_hash = store(service, "openvpn/ca", b"critical key material")
        blob_file = blob_path(storage_path / "openvpn/ca", full_hash)
        with blob_file.open("r+b") as f:
            f.truncate(5)

        result = runner.invoke(ctl_cli, ["verify"])

        assert result.exit_code == 5
        assert "Mismatched: 1" in result.output

    def test_missing_blob_exits_not_found(
        self, verify_env: tuple[CliRunner, Path, StorageService]
    ) -> None:
        runner, storage_path, service = verify_env
        full_hash = store(service, "openvpn/ca", b"critical key material")
        blob_path(storage_path / "openvpn/ca", full_hash).unlink()

        result = runner.invoke(ctl_cli, ["verify"])

        assert result.exit_code == 3
        assert "MISSING_BLOB: openvpn/ca" in result.output
        assert "Missing blob: 1" in result.output

    def test_missing_metadata_exits_not_found(
        self, verify_env: tuple[CliRunner, Path, StorageService]
    ) -> None:
        runner, storage_path, service = verify_env
        full_hash = store(service, "openvpn/ca", b"critical key material")
        metadata_path(storage_path / "openvpn/ca", full_hash).unlink()

        result = runner.invoke(ctl_cli, ["verify"])

        assert result.exit_code == 3
        assert "MISSING_METADATA: openvpn/ca" in result.output
        assert "Missing metadata: 1" in result.output

    def test_corrupt_metadata_exits_general_error(
        self, verify_env: tuple[CliRunner, Path, StorageService]
    ) -> None:
        runner, storage_path, service = verify_env
        full_hash = store(service, "openvpn/ca", b"critical key material")
        metadata_path(storage_path / "openvpn/ca", full_hash).write_text(
            "{ not json", encoding="utf-8"
        )

        result = runner.invoke(ctl_cli, ["verify"])

        assert result.exit_code == 1
        assert "Corrupt metadata: 1" in result.output

    def test_mismatch_outranks_missing_in_exit_code(
        self, verify_env: tuple[CliRunner, Path, StorageService]
    ) -> None:
        runner, storage_path, service = verify_env
        corrupt_hash = store(service, "openvpn/ca", b"critical key material")
        blob_path(storage_path / "openvpn/ca", corrupt_hash).write_bytes(b"tampered!!!!!!!!!!!!!")
        missing_hash = store(service, "images/ubuntu", b"disk image bytes")
        blob_path(storage_path / "images/ubuntu", missing_hash).unlink()

        result = runner.invoke(ctl_cli, ["verify"])

        assert result.exit_code == 5


class TestVerifyScopingAndBounds:
    """Scoping and work-bounding options."""

    def test_path_scope_skips_other_artifacts(
        self, verify_env: tuple[CliRunner, Path, StorageService]
    ) -> None:
        runner, storage_path, service = verify_env
        store(service, "openvpn/ca", b"ca bytes")
        bad_hash = store(service, "images/ubuntu", b"disk image bytes")
        blob_path(storage_path / "images/ubuntu", bad_hash).write_bytes(b"corrupted bytes!")

        result = runner.invoke(ctl_cli, ["verify", "--path", "openvpn"])

        assert result.exit_code == 0
        assert "Blobs scanned: 1" in result.output

    def test_unknown_path_scope_errors(
        self, verify_env: tuple[CliRunner, Path, StorageService]
    ) -> None:
        runner, _, service = verify_env
        store(service, "openvpn/ca", b"ca bytes")

        result = runner.invoke(ctl_cli, ["verify", "--path", "nope"])

        assert result.exit_code == 1
        assert "Path not found in storage" in result.output

    def test_traversal_path_scope_rejected(
        self, verify_env: tuple[CliRunner, Path, StorageService]
    ) -> None:
        runner, _, _ = verify_env

        result = runner.invoke(ctl_cli, ["verify", "--path", "../etc"])

        assert result.exit_code == 1

    def test_limit_bounds_the_scrub(
        self, verify_env: tuple[CliRunner, Path, StorageService]
    ) -> None:
        runner, _, service = verify_env
        for i in range(3):
            store(service, f"images/img{i}", f"content {i}".encode())

        result = runner.invoke(ctl_cli, ["verify", "--limit", "1"])

        assert result.exit_code == 0
        assert "Blobs scanned: 1" in result.output
        assert "Stopped early: work bound reached" in result.output

    def test_max_bytes_bounds_the_scrub(
        self, verify_env: tuple[CliRunner, Path, StorageService]
    ) -> None:
        runner, _, service = verify_env
        for i in range(3):
            store(service, f"images/img{i}", b"0123456789")

        result = runner.invoke(ctl_cli, ["verify", "--max-bytes", "5"])

        assert result.exit_code == 0
        assert "Blobs scanned: 1" in result.output

    def test_max_issues_caps_listing_but_not_counts(
        self, verify_env: tuple[CliRunner, Path, StorageService]
    ) -> None:
        runner, storage_path, service = verify_env
        for i in range(3):
            full_hash = store(service, f"images/img{i}", f"content {i}".encode())
            blob_path(storage_path / f"images/img{i}", full_hash).write_bytes(b"bad")

        result = runner.invoke(ctl_cli, ["verify", "--max-issues", "1"])

        assert result.exit_code == 5
        assert "Mismatched: 3" in result.output
        assert "2 more issue(s) not shown" in result.output

    def test_quiet_suppresses_progress(
        self, verify_env: tuple[CliRunner, Path, StorageService]
    ) -> None:
        runner, _, service = verify_env
        store(service, "openvpn/ca", b"ca bytes")

        result = runner.invoke(ctl_cli, ["verify", "--quiet"])

        assert result.exit_code == 0
        assert "Verify Summary:" in result.output


class TestVerifyJsonOutput:
    """Structured output for monitoring."""

    def test_json_output_shape_when_clean(
        self, verify_env: tuple[CliRunner, Path, StorageService]
    ) -> None:
        runner, _, service = verify_env
        store(service, "openvpn/ca", b"ca bytes")

        result = runner.invoke(ctl_cli, ["--format", "json", "verify"])

        assert result.exit_code == 0
        response = json.loads(result.output)
        assert set(response.keys()) == {"status", "data"}
        assert response["status"] == "ok"

        data = response["data"]
        for name in (
            "artifacts_scanned",
            "blobs_scanned",
            "bytes_read",
            "ok",
            "mismatched",
            "missing_blob",
            "missing_metadata",
            "corrupt_metadata",
            "errors",
        ):
            assert isinstance(data[name], int), f"{name} must be an int"
        assert data["stopped_early"] is False
        assert data["issues"] == []
        assert data["path_prefix"] is None

    def test_json_output_reports_issues_and_nonzero_exit(
        self, verify_env: tuple[CliRunner, Path, StorageService]
    ) -> None:
        runner, storage_path, service = verify_env
        full_hash = store(service, "openvpn/ca", b"critical key material")
        blob_path(storage_path / "openvpn/ca", full_hash).write_bytes(b"tampered!!!!!!!!!!!!!")

        result = runner.invoke(ctl_cli, ["--format", "json", "verify", "--path", "openvpn"])

        assert result.exit_code == 5
        data = json.loads(result.output)["data"]
        assert data["path_prefix"] == "openvpn"
        assert data["mismatched"] == 1
        issue = data["issues"][0]
        assert issue["status"] == "mismatch"
        assert issue["artifact_path"] == "openvpn/ca"
        assert issue["expected_hash"] == full_hash
        assert issue["actual_hash"] != full_hash

    def test_json_output_has_no_progress_noise(
        self, verify_env: tuple[CliRunner, Path, StorageService]
    ) -> None:
        runner, _, service = verify_env
        store(service, "openvpn/ca", b"ca bytes")

        result = runner.invoke(ctl_cli, ["--format", "json", "verify"])

        assert result.output.lstrip().startswith("{")
