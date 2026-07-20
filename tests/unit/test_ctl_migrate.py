"""Unit tests for the CTL migrate command (issue #561)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from magpie.auth.database import get_connection, init_database
from magpie.config import MagpieSettings
from magpie.ctl import cli
from magpie.ctl.commands.migrate import (
    _MIGRATIONS,
    CURRENT_DATA_FORMAT_VERSION,
    MigrationStep,
    _check_current_version_matches_migrations,
    get_data_format_version,
    run_migrations,
)


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def test_settings(tmp_path: Path) -> MagpieSettings:
    """Create test MagpieSettings with temporary paths."""
    return MagpieSettings(
        storage_path=tmp_path / "storage",
        database_path=tmp_path / "magpie.db",
    )


class TestGetDataFormatVersion:
    """Tests for the PRAGMA user_version read helper."""

    def test_fresh_connection_reads_zero(self, tmp_path: Path) -> None:
        """A brand-new (never-migrated) database reads version 0."""
        db_path = tmp_path / "fresh.db"
        init_database(db_path)
        conn = get_connection(db_path)
        try:
            assert get_data_format_version(conn) == 0
        finally:
            conn.close()


class TestRunMigrations:
    """Tests for the migration-application logic, independent of the CLI."""

    def test_applies_pending_steps_and_advances_version(self, tmp_path: Path) -> None:
        """A database at version 0 is advanced to CURRENT_DATA_FORMAT_VERSION."""
        db_path = tmp_path / "magpie.db"
        init_database(db_path)
        conn = get_connection(db_path)
        try:
            version_before, version_after, applied = run_migrations(conn)
        finally:
            conn.close()

        assert version_before == 0
        assert version_after == CURRENT_DATA_FORMAT_VERSION
        # Against len(_MIGRATIONS) (the actual step count), not
        # CURRENT_DATA_FORMAT_VERSION -- the two only coincide today
        # because there's a single step at version 1. A registry with a
        # gap (e.g. steps at versions 1 and 5, CURRENT=5) would have
        # len(applied) == 2, not 5.
        assert len(applied) == len(_MIGRATIONS)

    def test_idempotent_on_already_current_database(self, tmp_path: Path) -> None:
        """Running migrations twice applies nothing the second time."""
        db_path = tmp_path / "magpie.db"
        init_database(db_path)
        conn = get_connection(db_path)
        try:
            run_migrations(conn)
            version_before, version_after, applied = run_migrations(conn)
        finally:
            conn.close()

        assert version_before == CURRENT_DATA_FORMAT_VERSION
        assert version_after == CURRENT_DATA_FORMAT_VERSION
        assert applied == []

    def test_persists_across_connections(self, tmp_path: Path) -> None:
        """The stamped version survives closing and reopening the database."""
        db_path = tmp_path / "magpie.db"
        init_database(db_path)
        conn = get_connection(db_path)
        try:
            run_migrations(conn)
        finally:
            conn.close()

        conn2 = get_connection(db_path)
        try:
            assert get_data_format_version(conn2) == CURRENT_DATA_FORMAT_VERSION
        finally:
            conn2.close()

    def test_skips_steps_at_or_below_current_version(self, tmp_path: Path) -> None:
        """A step whose version is <= the stamped version is never re-applied.

        Verified by pre-stamping the database to CURRENT_DATA_FORMAT_VERSION
        directly (bypassing run_migrations()) and confirming a mutating
        sentinel step would not run -- exercised via a synthetic step list
        rather than monkeypatching the real (empty) v1 step, so this test
        would actually fail if the skip condition were ever loosened to
        "!=" instead of "<=".
        """
        db_path = tmp_path / "magpie.db"
        init_database(db_path)
        conn = get_connection(db_path)
        try:
            conn.execute("PRAGMA user_version = 5")
            conn.commit()

            calls: list[int] = []

            def sentinel(c: sqlite3.Connection) -> None:
                calls.append(1)

            fake_steps = (
                MigrationStep(1, "old step", sentinel),
                MigrationStep(5, "already applied", sentinel),
                MigrationStep(6, "new step", sentinel),
            )
            with patch("magpie.ctl.commands.migrate._MIGRATIONS", fake_steps):
                version_before, version_after, applied = run_migrations(conn)
        finally:
            conn.close()

        assert version_before == 5
        assert version_after == 6
        assert applied == ["v6: new step"]
        assert calls == [1]


class TestCurrentVersionMatchesMigrations:
    """Tests for the CURRENT_DATA_FORMAT_VERSION/_MIGRATIONS drift guard.

    The real module-level call at import time already proves the two are
    in sync today (otherwise nothing in this file would have imported
    successfully) -- these exercise the guard function directly against
    synthetic inputs, including the mismatch case the real call can never
    demonstrate against itself.
    """

    def test_matching_version_is_a_no_op(self) -> None:
        """A last step whose version equals current_version raises nothing."""
        steps = (MigrationStep(1, "a", lambda c: None), MigrationStep(3, "b", lambda c: None))
        _check_current_version_matches_migrations(3, steps)  # must not raise

    def test_current_version_ahead_of_migrations_raises(self) -> None:
        """A bumped CURRENT_DATA_FORMAT_VERSION with no matching new step is caught."""
        steps = (MigrationStep(1, "a", lambda c: None),)
        with pytest.raises(AssertionError, match="does not match"):
            _check_current_version_matches_migrations(2, steps)

    def test_migrations_ahead_of_current_version_raises(self) -> None:
        """A new step registered without bumping CURRENT_DATA_FORMAT_VERSION is caught."""
        steps = (MigrationStep(1, "a", lambda c: None), MigrationStep(2, "b", lambda c: None))
        with pytest.raises(AssertionError, match="does not match"):
            _check_current_version_matches_migrations(1, steps)

    def test_empty_migrations_treated_as_version_zero(self) -> None:
        """An empty registry is only valid alongside CURRENT_DATA_FORMAT_VERSION == 0."""
        _check_current_version_matches_migrations(0, ())  # must not raise
        with pytest.raises(AssertionError, match="does not match"):
            _check_current_version_matches_migrations(1, ())


class TestMigrateCommand:
    """Tests for the `magpie-ctl migrate` CLI command."""

    def test_migrate_stamps_fresh_database(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Running migrate against a database that doesn't exist yet creates and stamps it."""
        assert not test_settings.database_path.exists()
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["migrate"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert test_settings.database_path.exists()
        assert f"v0 -> v{CURRENT_DATA_FORMAT_VERSION}" in result.output

    def test_migrate_is_idempotent(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Running migrate twice reports nothing to do the second time."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            first = cli_runner.invoke(cli, ["migrate"])
            second = cli_runner.invoke(cli, ["migrate"])

        assert first.exit_code == 0, f"Output: {first.output}"
        assert second.exit_code == 0, f"Output: {second.output}"
        assert "already current" in second.output
        assert "nothing to do" in second.output

    def test_migrate_check_does_not_mutate(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """--check reports the version without creating a database or stamping anything."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["migrate", "--check"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Data-format version: 0" in result.output
        assert not test_settings.database_path.exists()

    def test_migrate_check_reflects_prior_migrate(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """--check after a real migrate reports the advanced version."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            cli_runner.invoke(cli, ["migrate"])
            result = cli_runner.invoke(cli, ["migrate", "--check"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert f"Data-format version: {CURRENT_DATA_FORMAT_VERSION}" in result.output

    def test_migrate_json_output(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """--format json reports version_before/version_after/applied."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["--format", "json", "migrate"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert '"version_before": 0' in result.output
        assert f'"version_after": {CURRENT_DATA_FORMAT_VERSION}' in result.output

    def test_migrate_check_json_output(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """--format json --check reports data_format_version/current_data_format_version."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["--format", "json", "migrate", "--check"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert '"data_format_version": 0' in result.output
        assert f'"current_data_format_version": {CURRENT_DATA_FORMAT_VERSION}' in result.output
