"""Unit tests for the CTL migrate command (issue #561)."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from magpie.auth.database import create_schema, get_connection, init_database
from magpie.config import MagpieSettings
from magpie.ctl import cli
from magpie.ctl.commands.migrate import (
    _MIGRATIONS,
    CURRENT_DATA_FORMAT_VERSION,
    MigrationStep,
    MigrationTargetError,
    _check_current_version_matches_migrations,
    check_migrations_reversibility,
    get_data_format_version,
    migrate_database,
    revert_database,
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
            # CURRENT_DATA_FORMAT_VERSION is also patched to match the
            # synthetic steps' own top version (6) -- the real constant is
            # 1, and the stamped version 5 above is deliberately higher
            # than that to exercise "already applied", which would
            # otherwise now trip the newer-than-supported fail-closed
            # guard this test isn't about.
            with (
                patch("magpie.ctl.commands.migrate._MIGRATIONS", fake_steps),
                patch("magpie.ctl.commands.migrate.CURRENT_DATA_FORMAT_VERSION", 6),
            ):
                version_before, version_after, applied = run_migrations(conn)
        finally:
            conn.close()

        assert version_before == 5
        assert version_after == 6
        assert applied == ["v6: new step"]
        assert calls == [1]

    def test_fails_closed_on_newer_than_supported_stamp(self, tmp_path: Path) -> None:
        """A database stamped newer than CURRENT_DATA_FORMAT_VERSION is refused, not treated as current.

        The likely real-world cause: an older magpie build (this one)
        running against data a newer build already migrated -- e.g. a
        downgrade, or `magpie-deploy.sh update --no-rollback` followed by
        a manual revert. Silently no-op'ing here (nothing in _MIGRATIONS
        exceeds the stamped version, so the loop would apply nothing and
        report success) would let this build run against data it doesn't
        actually understand.
        """
        db_path = tmp_path / "magpie.db"
        init_database(db_path)
        conn = get_connection(db_path)
        try:
            conn.execute(f"PRAGMA user_version = {CURRENT_DATA_FORMAT_VERSION + 1}")
            conn.commit()

            with pytest.raises(ValueError, match="newer than this"):
                run_migrations(conn)
        finally:
            conn.close()

        # Refusing to migrate must not itself mutate the stamp.
        conn2 = get_connection(db_path)
        try:
            assert get_data_format_version(conn2) == CURRENT_DATA_FORMAT_VERSION + 1
        finally:
            conn2.close()


class TestRunMigrationsTransactional:
    """run_migrations() is all-or-nothing and serializes concurrent callers."""

    def test_noop_inside_callers_transaction_keeps_pending_writes(self, tmp_path: Path) -> None:
        """With a transaction already open, run_migrations() neither fails nor commits it."""
        db_path = tmp_path / "magpie.db"
        init_database(db_path)
        conn = get_connection(db_path)
        try:
            run_migrations(conn)
            conn.execute(
                "INSERT INTO tokens (name, token_hash, scope, enabled, created_at) "
                "VALUES ('pending', 'h', 'read', 1, '2026-01-01T00:00:00')"
            )
            assert conn.in_transaction
            _, _, applied = run_migrations(conn)
            assert applied == []
            assert conn.in_transaction
            conn.rollback()
            assert conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0] == 0
        finally:
            conn.close()

    def test_stale_read_snapshot_cannot_hide_a_newer_stamp(self, tmp_path: Path) -> None:
        """A caller's WAL read snapshot taken before a newer stamp must not pass as current."""
        db_path = tmp_path / "magpie.db"
        init_database(db_path)
        conn_a = get_connection(db_path)
        conn_b = get_connection(db_path)
        try:
            run_migrations(conn_a)
            conn_a.execute("BEGIN")
            assert get_data_format_version(conn_a) == CURRENT_DATA_FORMAT_VERSION
            conn_b.execute(f"PRAGMA user_version = {CURRENT_DATA_FORMAT_VERSION + 1}")
            conn_b.commit()

            with pytest.raises((sqlite3.OperationalError, ValueError)):
                run_migrations(conn_a)
            conn_a.rollback()
        finally:
            conn_a.close()
            conn_b.close()

    def test_failing_step_inside_callers_transaction_rolls_back_only_itself(
        self, tmp_path: Path
    ) -> None:
        """A failed step inside a caller's transaction undoes the migration, not the caller's writes."""
        db_path = tmp_path / "magpie.db"
        init_database(db_path)

        def create_then_fail(c: sqlite3.Connection) -> None:
            c.execute("CREATE TABLE migrated_marker (x INTEGER)")
            raise RuntimeError("step failed")

        conn = get_connection(db_path)
        try:
            conn.execute(
                "INSERT INTO tokens (name, token_hash, scope, enabled, created_at) "
                "VALUES ('pending', 'h', 'read', 1, '2026-01-01T00:00:00')"
            )
            with (
                patch(
                    "magpie.ctl.commands.migrate._MIGRATIONS",
                    (MigrationStep(1, "fail", create_then_fail),),
                ),
                pytest.raises(RuntimeError, match="step failed"),
            ):
                run_migrations(conn)
            assert conn.in_transaction
            conn.commit()
        finally:
            conn.close()

        conn2 = sqlite3.connect(db_path)
        try:
            assert get_data_format_version(conn2) == 0
            tables = {
                row[0] for row in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            assert "migrated_marker" not in tables
            assert conn2.execute("SELECT name FROM tokens").fetchall() == [("pending",)]
        finally:
            conn2.close()

    def test_failing_step_rolls_back_earlier_steps_and_stamp(self, tmp_path: Path) -> None:
        """A step that raises leaves the database exactly as it was before the run."""
        db_path = tmp_path / "magpie.db"
        init_database(db_path)

        def create_table(c: sqlite3.Connection) -> None:
            c.execute("CREATE TABLE migrated_marker (x INTEGER)")
            c.execute("INSERT INTO migrated_marker VALUES (1)")

        def boom(c: sqlite3.Connection) -> None:
            raise RuntimeError("step 2 failed")

        fake_steps = (MigrationStep(1, "create", create_table), MigrationStep(2, "boom", boom))
        conn = get_connection(db_path)
        try:
            with (
                patch("magpie.ctl.commands.migrate._MIGRATIONS", fake_steps),
                patch("magpie.ctl.commands.migrate.CURRENT_DATA_FORMAT_VERSION", 2),
                pytest.raises(RuntimeError, match="step 2 failed"),
            ):
                run_migrations(conn)
            assert not conn.in_transaction
        finally:
            conn.close()

        conn2 = sqlite3.connect(db_path)
        try:
            assert get_data_format_version(conn2) == 0
            tables = {
                row[0] for row in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            assert "migrated_marker" not in tables
        finally:
            conn2.close()

    def test_holds_write_lock_while_steps_run(self, tmp_path: Path) -> None:
        """Steps run under BEGIN IMMEDIATE, so a concurrent writer is locked out."""
        db_path = tmp_path / "magpie.db"
        init_database(db_path)
        observed: list[str] = []

        def probe(c: sqlite3.Connection) -> None:
            other = sqlite3.connect(db_path, timeout=0)
            try:
                other.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as e:
                observed.append(str(e))
            finally:
                other.close()

        conn = get_connection(db_path)
        try:
            with (
                patch("magpie.ctl.commands.migrate._MIGRATIONS", (MigrationStep(1, "p", probe),)),
                patch("magpie.ctl.commands.migrate.CURRENT_DATA_FORMAT_VERSION", 1),
            ):
                run_migrations(conn)
        finally:
            conn.close()

        assert observed and "locked" in observed[0]

    def test_second_caller_sees_first_callers_stamp(self, tmp_path: Path) -> None:
        """Two connections migrating the same database apply each step exactly once."""
        db_path = tmp_path / "magpie.db"
        init_database(db_path)
        conn_a = get_connection(db_path)
        conn_b = get_connection(db_path)
        try:
            _, _, applied_a = run_migrations(conn_a)
            before_b, after_b, applied_b = run_migrations(conn_b)
        finally:
            conn_a.close()
            conn_b.close()

        assert len(applied_a) == len(_MIGRATIONS)
        assert applied_b == []
        assert before_b == after_b == CURRENT_DATA_FORMAT_VERSION

    def test_newer_than_supported_message_names_both_versions_and_remedy(
        self, tmp_path: Path
    ) -> None:
        """The refusal says which versions are involved and what the operator should do."""
        db_path = tmp_path / "magpie.db"
        init_database(db_path)
        newer = CURRENT_DATA_FORMAT_VERSION + 1
        conn = get_connection(db_path)
        try:
            conn.execute(f"PRAGMA user_version = {newer}")
            conn.commit()
            with pytest.raises(ValueError) as excinfo:
                run_migrations(conn)
            assert not conn.in_transaction
        finally:
            conn.close()

        message = str(excinfo.value)
        assert f"({newer})" in message
        assert f"current: {CURRENT_DATA_FORMAT_VERSION}" in message
        assert "newer" in message
        assert "restore a backup" in message
        assert "\n" not in message


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

    def test_migrate_fails_closed_on_newer_than_supported_stamp(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """`magpie-ctl migrate` reports a clean error (not a traceback) for a downgrade scenario."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            cli_runner.invoke(cli, ["migrate"])
            conn = get_connection(test_settings.database_path)
            try:
                conn.execute(f"PRAGMA user_version = {CURRENT_DATA_FORMAT_VERSION + 1}")
                conn.commit()
            finally:
                conn.close()

            result = cli_runner.invoke(cli, ["migrate"])

        assert result.exit_code != 0, f"Output: {result.output}"
        assert "newer than this" in result.output
        # A clean, structured error -- not an unhandled-exception traceback.
        assert "Traceback" not in result.output

    def test_migrate_quiet_suppresses_already_current_message(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """--quiet prints nothing when there is nothing to do."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            cli_runner.invoke(cli, ["migrate"])
            result = cli_runner.invoke(cli, ["migrate", "--quiet"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert result.output == ""

    def test_migrate_quiet_still_reports_applied_steps(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """--quiet only hides the no-op message; a real migration is still logged."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["migrate", "-q"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert f"v0 -> v{CURRENT_DATA_FORMAT_VERSION}" in result.output

    def test_migrate_refuses_newer_database_without_touching_it(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """A newer-than-supported database is refused before init_database() runs.

        The database here deliberately has no tokens table: if migrate ran
        init_database() before refusing, the table would appear.
        """
        db_path = test_settings.database_path
        newer = CURRENT_DATA_FORMAT_VERSION + 1
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(f"PRAGMA user_version = {newer}")
            conn.commit()
        finally:
            conn.close()
        before = db_path.read_bytes()

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["migrate", "--quiet"])

        assert result.exit_code != 0, f"Output: {result.output}"
        assert f"({newer})" in result.output
        assert f"current: {CURRENT_DATA_FORMAT_VERSION}" in result.output
        assert "Traceback" not in result.output
        assert db_path.read_bytes() == before
        conn = sqlite3.connect(db_path)
        try:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master")]
        finally:
            conn.close()
        assert tables == []

    def test_version_check_and_schema_creation_share_one_write_lock(self, tmp_path: Path) -> None:
        """No other writer can stamp the database between the version check and init.

        Otherwise a newer build could advance the stamp after this build's
        check and this build would then create its own schema on data it
        doesn't understand before refusing.
        """
        db_path = tmp_path / "magpie.db"
        observed: list[str] = []

        def probing_create_schema(c: sqlite3.Connection) -> None:
            assert c.in_transaction
            other = sqlite3.connect(db_path, timeout=0)
            try:
                other.execute("PRAGMA user_version = 99")
            except sqlite3.OperationalError as e:
                observed.append(str(e))
            finally:
                other.close()
            create_schema(c)

        with patch("magpie.ctl.commands.migrate.create_schema", probing_create_schema):
            migrate_database(db_path)

        assert observed and "locked" in observed[0]
        conn = sqlite3.connect(db_path)
        try:
            assert get_data_format_version(conn) == CURRENT_DATA_FORMAT_VERSION
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            conn.close()

    def test_wal_switch_failure_is_reported_as_committed_not_rolled_back(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """A reader blocking the post-commit WAL switch fails migrate, with an accurate message."""
        db_path = test_settings.database_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        legacy = sqlite3.connect(db_path)
        try:
            legacy.execute("CREATE TABLE legacy (x INTEGER)")
            legacy.commit()
        finally:
            legacy.close()

        reader = sqlite3.connect(db_path)
        real_connect = sqlite3.connect

        class ReaderAfterCommit(sqlite3.Connection):
            def commit(self) -> None:
                super().commit()
                reader.execute("BEGIN")
                reader.execute("SELECT * FROM legacy").fetchall()

        def connect(path: Path) -> sqlite3.Connection:
            return real_connect(path, timeout=0, factory=ReaderAfterCommit)

        try:
            with (
                patch("magpie.ctl.get_settings", return_value=test_settings),
                patch("magpie.ctl.commands.migrate.sqlite3.connect", connect),
            ):
                result = cli_runner.invoke(cli, ["migrate", "--quiet"])
            reader.rollback()
        finally:
            reader.close()

        assert result.exit_code != 0, f"Output: {result.output}"
        assert f"committed (now v{CURRENT_DATA_FORMAT_VERSION})" in result.output
        assert "WAL" in result.output
        assert "rolled back" not in result.output
        assert "Traceback" not in result.output
        conn = sqlite3.connect(db_path)
        try:
            assert get_data_format_version(conn) == CURRENT_DATA_FORMAT_VERSION
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        finally:
            conn.close()

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            rerun = cli_runner.invoke(cli, ["migrate", "--quiet"])
        assert rerun.exit_code == 0, f"Output: {rerun.output}"
        assert rerun.output == ""

    def test_noop_migrate_database_does_not_write(self, tmp_path: Path) -> None:
        """Re-migrating an already-current database appends nothing to the WAL."""
        db_path = tmp_path / "magpie.db"
        wal_path = tmp_path / "magpie.db-wal"
        migrate_database(db_path)
        # An open connection keeps the WAL file from being removed on close.
        holder = sqlite3.connect(db_path)
        try:
            holder.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            assert wal_path.stat().st_size == 0

            migrate_database(db_path)

            assert wal_path.stat().st_size == 0
        finally:
            holder.close()

    def test_migrate_reports_failed_step_cleanly_and_leaves_database_unchanged(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """A sqlite error inside a step is a clean non-zero exit with nothing applied."""

        def bad_sql(c: sqlite3.Connection) -> None:
            c.execute("ALTER TABLE does_not_exist ADD COLUMN x INTEGER")

        init_database(test_settings.database_path)
        with (
            patch("magpie.ctl.get_settings", return_value=test_settings),
            patch("magpie.ctl.commands.migrate._MIGRATIONS", (MigrationStep(1, "bad", bad_sql),)),
        ):
            result = cli_runner.invoke(cli, ["migrate"])

        assert result.exit_code != 0, f"Output: {result.output}"
        assert "rolled back" in result.output
        assert "Traceback" not in result.output
        conn = sqlite3.connect(test_settings.database_path)
        try:
            assert get_data_format_version(conn) == 0
        finally:
            conn.close()

    def test_migrate_reports_unreadable_database_cleanly(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """A file that isn't a SQLite database is a clean non-zero exit, left as-is."""
        db_path = test_settings.database_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        garbage = b"this is not a sqlite database" * 100
        db_path.write_bytes(garbage)

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["migrate"])

        assert result.exit_code != 0, f"Output: {result.output}"
        assert "failed and was rolled back" in result.output
        assert "Traceback" not in result.output
        assert db_path.read_bytes() == garbage


def _noop(conn: sqlite3.Connection) -> None:
    """A step body that changes nothing."""


def _snapshot(conn: sqlite3.Connection) -> list[str]:
    """Schema and data of ``conn`` as SQL statements (excludes the version stamp)."""
    return list(conn.iterdump())


def _stamped_db(db_path: Path, version: int) -> None:
    """Create a schema'd database with one token row, stamped ``version``."""
    init_database(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tokens (name, token_hash, scope, enabled, created_at) "
            "VALUES ('pre-existing', 'h', 'read', 1, '2026-01-01T00:00:00')"
        )
        conn.execute(f"PRAGMA user_version = {int(version)}")
        conn.commit()
    finally:
        conn.close()


def _version(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return get_data_format_version(conn)
    finally:
        conn.close()


class TestMigrationReversibility:
    """Every step is reversible or explicitly irreversible, and reverts round-trip."""

    def test_every_registered_step_has_revert_or_irreversible_marker(self) -> None:
        """A registered step with neither a revert nor irreversible=True fails here."""
        check_migrations_reversibility(_MIGRATIONS)

    def test_step_with_neither_is_rejected(self) -> None:
        with pytest.raises(AssertionError, match="v2 .* has no revert"):
            check_migrations_reversibility(
                (MigrationStep(1, "ok", _noop, revert=_noop), MigrationStep(2, "bare", _noop))
            )

    def test_step_with_both_is_rejected(self) -> None:
        with pytest.raises(AssertionError, match="marked irreversible"):
            check_migrations_reversibility(
                (MigrationStep(1, "both", _noop, revert=_noop, irreversible=True),)
            )

    def test_irreversible_step_is_accepted(self) -> None:
        check_migrations_reversibility((MigrationStep(1, "one-way", _noop, irreversible=True),))

    @pytest.mark.parametrize("step", _MIGRATIONS, ids=lambda step: f"v{step.version}")
    def test_apply_then_revert_round_trips(self, step: MigrationStep, tmp_path: Path) -> None:
        """Applying a step and then reverting it restores the starting schema and data."""
        if step.irreversible:
            pytest.skip(f"v{step.version} is marked irreversible")
        assert step.revert is not None
        db_path = tmp_path / "magpie.db"
        _stamped_db(db_path, 0)
        conn = sqlite3.connect(db_path)
        try:
            for earlier in _MIGRATIONS:
                if earlier.version < step.version:
                    earlier.apply(conn)
            conn.commit()
            before = _snapshot(conn)

            step.apply(conn)
            step.revert(conn)
            conn.commit()

            assert _snapshot(conn) == before
        finally:
            conn.close()


class TestRevertDatabase:
    """revert_database(): `migrate --to N` without the CLI."""

    def test_reverts_current_database_to_zero(self, tmp_path: Path) -> None:
        db_path = tmp_path / "magpie.db"
        migrate_database(db_path)

        version_before, version_after, reverted = revert_database(db_path, 0)

        assert (version_before, version_after) == (CURRENT_DATA_FORMAT_VERSION, 0)
        assert len(reverted) == len(_MIGRATIONS)
        assert _version(db_path) == 0

    def test_reverted_database_migrates_forward_again(self, tmp_path: Path) -> None:
        db_path = tmp_path / "magpie.db"
        migrate_database(db_path)
        revert_database(db_path, 0)

        _, version_after, applied = migrate_database(db_path)

        assert version_after == CURRENT_DATA_FORMAT_VERSION
        assert len(applied) == len(_MIGRATIONS)

    def test_reverts_newest_first_and_stamps_last(self, tmp_path: Path) -> None:
        db_path = tmp_path / "magpie.db"
        _stamped_db(db_path, 3)
        seen: list[tuple[int, int]] = []

        def recorder(version: int):  # noqa: ANN202
            def revert(conn: sqlite3.Connection) -> None:
                seen.append((version, get_data_format_version(conn)))

            return revert

        steps = tuple(MigrationStep(v, f"s{v}", _noop, revert=recorder(v)) for v in (1, 2, 3))
        with (
            patch("magpie.ctl.commands.migrate._MIGRATIONS", steps),
            patch("magpie.ctl.commands.migrate.CURRENT_DATA_FORMAT_VERSION", 3),
        ):
            _, version_after, reverted = revert_database(db_path, 1)

        assert seen == [(3, 3), (2, 3)]
        assert reverted == ["v3: s3", "v2: s2"]
        assert version_after == 1
        assert _version(db_path) == 1

    def test_failed_revert_rolls_everything_back(self, tmp_path: Path) -> None:
        db_path = tmp_path / "magpie.db"
        _stamped_db(db_path, 2)

        def delete_tokens(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM tokens")

        def bad_sql(conn: sqlite3.Connection) -> None:
            conn.execute("SELECT * FROM no_such_table")

        steps = (
            MigrationStep(1, "fails", _noop, revert=bad_sql),
            MigrationStep(2, "deletes", _noop, revert=delete_tokens),
        )
        with (
            patch("magpie.ctl.commands.migrate._MIGRATIONS", steps),
            patch("magpie.ctl.commands.migrate.CURRENT_DATA_FORMAT_VERSION", 2),
            pytest.raises(sqlite3.OperationalError),
        ):
            revert_database(db_path, 0)

        assert _version(db_path) == 2
        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("SELECT name FROM tokens").fetchall() == [("pre-existing",)]
        finally:
            conn.close()

    def test_refuses_to_cross_an_irreversible_step(self, tmp_path: Path) -> None:
        db_path = tmp_path / "magpie.db"
        _stamped_db(db_path, 2)
        calls: list[int] = []

        def revert_1(conn: sqlite3.Connection) -> None:
            calls.append(1)

        steps = (
            MigrationStep(1, "reversible", _noop, revert=revert_1),
            MigrationStep(2, "one-way", _noop, irreversible=True),
        )
        before = db_path.read_bytes()
        with (
            patch("magpie.ctl.commands.migrate._MIGRATIONS", steps),
            patch("magpie.ctl.commands.migrate.CURRENT_DATA_FORMAT_VERSION", 2),
        ):
            with pytest.raises(ValueError, match=r"irreversible.*v2 \(one-way\)"):
                revert_database(db_path, 0)
            with pytest.raises(ValueError, match="irreversible"):
                revert_database(db_path, 1)

        assert calls == []
        assert db_path.read_bytes() == before

    def test_reverts_down_to_an_irreversible_step(self, tmp_path: Path) -> None:
        """An irreversible step below the target is not crossed, so it doesn't block."""
        db_path = tmp_path / "magpie.db"
        _stamped_db(db_path, 2)
        steps = (
            MigrationStep(1, "one-way", _noop, irreversible=True),
            MigrationStep(2, "reversible", _noop, revert=_noop),
        )
        with (
            patch("magpie.ctl.commands.migrate._MIGRATIONS", steps),
            patch("magpie.ctl.commands.migrate.CURRENT_DATA_FORMAT_VERSION", 2),
        ):
            _, version_after, reverted = revert_database(db_path, 1)

        assert version_after == 1
        assert reverted == ["v2: reversible"]
        assert _version(db_path) == 1

    def test_refuses_target_above_current(self, tmp_path: Path) -> None:
        db_path = tmp_path / "magpie.db"
        _stamped_db(db_path, 0)
        before = db_path.read_bytes()

        with pytest.raises(MigrationTargetError, match="only moves down"):
            revert_database(db_path, 1)

        assert db_path.read_bytes() == before

    def test_refuses_unknown_target(self, tmp_path: Path) -> None:
        db_path = tmp_path / "magpie.db"
        _stamped_db(db_path, 5)
        steps = (
            MigrationStep(1, "a", _noop, revert=_noop),
            MigrationStep(5, "b", _noop, revert=_noop),
        )
        with (
            patch("magpie.ctl.commands.migrate._MIGRATIONS", steps),
            patch("magpie.ctl.commands.migrate.CURRENT_DATA_FORMAT_VERSION", 5),
            pytest.raises(MigrationTargetError, match="known: 0, 1, 5"),
        ):
            revert_database(db_path, 3)

        assert _version(db_path) == 5

    def test_refuses_newer_than_supported_database(self, tmp_path: Path) -> None:
        db_path = tmp_path / "magpie.db"
        _stamped_db(db_path, CURRENT_DATA_FORMAT_VERSION + 1)
        before = db_path.read_bytes()

        with pytest.raises(ValueError, match="newer than this"):
            revert_database(db_path, 0)

        assert db_path.read_bytes() == before

    def test_missing_database_is_refused_without_creating_one(self, tmp_path: Path) -> None:
        db_path = tmp_path / "magpie.db"

        with pytest.raises(FileNotFoundError):
            revert_database(db_path, 0)

        assert not db_path.exists()

    def test_noop_revert_does_not_write(self, tmp_path: Path) -> None:
        db_path = tmp_path / "magpie.db"
        wal_path = tmp_path / "magpie.db-wal"
        migrate_database(db_path)
        holder = sqlite3.connect(db_path)
        try:
            holder.execute("PRAGMA wal_checkpoint(TRUNCATE)")

            _, _, reverted = revert_database(db_path, CURRENT_DATA_FORMAT_VERSION)

            assert reverted == []
            assert wal_path.stat().st_size == 0
        finally:
            holder.close()

    def test_takes_the_write_lock_before_reading_the_version(self, tmp_path: Path) -> None:
        """A newer stamp committed by a concurrent writer is seen, not a stale version."""
        db_path = tmp_path / "magpie.db"
        migrate_database(db_path)
        writer = sqlite3.connect(db_path)
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(f"PRAGMA user_version = {CURRENT_DATA_FORMAT_VERSION + 1}")
        errors: list[BaseException] = []

        def run() -> None:
            try:
                revert_database(db_path, 0)
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        thread = threading.Thread(target=run)
        try:
            thread.start()
            time.sleep(0.3)
            assert thread.is_alive(), "revert_database() did not wait for the write lock"
            writer.commit()
            thread.join(timeout=10)
        finally:
            writer.close()

        assert len(errors) == 1
        assert "newer than this" in str(errors[0])
        assert _version(db_path) == CURRENT_DATA_FORMAT_VERSION + 1


class TestMigrateToCommand:
    """`magpie-ctl migrate --to N`."""

    def test_to_zero_reverts_and_reports(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            cli_runner.invoke(cli, ["migrate"])
            result = cli_runner.invoke(cli, ["migrate", "--to", "0"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert f"Reverted data format: v{CURRENT_DATA_FORMAT_VERSION} -> v0" in result.output
        assert _version(test_settings.database_path) == 0

    def test_to_json_output(self, cli_runner: CliRunner, test_settings: MagpieSettings) -> None:
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            cli_runner.invoke(cli, ["migrate"])
            result = cli_runner.invoke(cli, ["--format", "json", "migrate", "--to", "0"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert f'"version_before": {CURRENT_DATA_FORMAT_VERSION}' in result.output
        assert '"version_after": 0' in result.output
        assert '"reverted"' in result.output

    def test_to_current_is_a_quiet_no_op(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            cli_runner.invoke(cli, ["migrate"])
            loud = cli_runner.invoke(cli, ["migrate", "--to", str(CURRENT_DATA_FORMAT_VERSION)])
            quiet = cli_runner.invoke(
                cli, ["migrate", "--to", str(CURRENT_DATA_FORMAT_VERSION), "--quiet"]
            )

        assert loud.exit_code == 0, f"Output: {loud.output}"
        assert "nothing to revert" in loud.output
        assert quiet.exit_code == 0, f"Output: {quiet.output}"
        assert quiet.output == ""

    def test_to_above_current_is_refused(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            cli_runner.invoke(cli, ["migrate"])
            result = cli_runner.invoke(cli, ["migrate", "--to", "0"])
            result = cli_runner.invoke(cli, ["migrate", "--to", "1"])

        assert result.exit_code != 0, f"Output: {result.output}"
        assert "only moves down" in result.output
        assert "Traceback" not in result.output
        assert _version(test_settings.database_path) == 0

    def test_to_unknown_version_is_refused(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            cli_runner.invoke(cli, ["migrate"])
            result = cli_runner.invoke(
                cli, ["migrate", "--to", str(CURRENT_DATA_FORMAT_VERSION + 7)]
            )

        assert result.exit_code != 0, f"Output: {result.output}"
        assert "not one this build of magpie knows" in result.output
        assert _version(test_settings.database_path) == CURRENT_DATA_FORMAT_VERSION

    def test_negative_to_is_a_usage_error(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["migrate", "--to", "-1"])

        assert result.exit_code == 2, f"Output: {result.output}"

    def test_to_with_check_is_a_usage_error(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["migrate", "--check", "--to", "0"])

        assert result.exit_code == 2, f"Output: {result.output}"
        assert "cannot be used together" in result.output

    def test_to_without_database_is_refused(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["migrate", "--to", "0"])

        assert result.exit_code != 0, f"Output: {result.output}"
        assert "No database" in result.output
        assert not test_settings.database_path.exists()

    def test_to_across_irreversible_step_is_refused(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        _stamped_db(test_settings.database_path, 1)
        steps = (MigrationStep(1, "one-way", _noop, irreversible=True),)
        with (
            patch("magpie.ctl.get_settings", return_value=test_settings),
            patch("magpie.ctl.commands.migrate._MIGRATIONS", steps),
        ):
            result = cli_runner.invoke(cli, ["migrate", "--to", "0"])

        assert result.exit_code != 0, f"Output: {result.output}"
        assert "irreversible" in result.output
        assert "Restore a backup" in result.output
        assert _version(test_settings.database_path) == 1

    def test_newer_than_supported_refusal_suggests_migrate_to(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """The startup refusal tells the operator how to revert with the newer image."""
        _stamped_db(test_settings.database_path, CURRENT_DATA_FORMAT_VERSION + 1)
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["migrate", "--quiet"])

        assert result.exit_code != 0, f"Output: {result.output}"
        assert f"magpie-ctl migrate --to {CURRENT_DATA_FORMAT_VERSION}" in result.output
        assert "restore a backup" in result.output

    def test_plain_migrate_never_moves_down(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Without --to (the container startup path), the stamp only ever goes up."""
        _stamped_db(test_settings.database_path, CURRENT_DATA_FORMAT_VERSION + 1)
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            cli_runner.invoke(cli, ["migrate", "--quiet"])

        assert _version(test_settings.database_path) == CURRENT_DATA_FORMAT_VERSION + 1
