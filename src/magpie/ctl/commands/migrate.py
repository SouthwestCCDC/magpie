"""Migrate command for the data-format version marker (issue #561).

Provides the forward-only migration mechanism the bundled image's
wrapper.sh runs on every container start (before serving traffic, under
the init lock) and `magpie-deploy.sh update` also calls after swapping in
a new image, so a future release that actually
changes the token DB schema or the on-disk /data layout has somewhere to
register a transform. As of v0.2.0 there is nothing to transform (the
bundled single-container image reads the same /data layout the pre-0.2.0
two-container topology used), so this only stamps a baseline version.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, NamedTuple

import click

from magpie.auth.database import create_schema
from magpie.cli.formatting import (
    CommandResult,
    ErrorCode,
    is_json_output,
    output_error,
    output_result,
)
from magpie.ctl import CTLContext

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The data-format version this build of magpie expects. Bumped whenever a
# future release needs to transform existing data to match a schema/layout
# change -- each bump registers a new step in _MIGRATIONS, and this
# constant becomes that step's version. Read back by
# `magpie-deploy.sh update`'s assert_post_update() (via --check) to confirm
# a migration actually advanced the stamp.
CURRENT_DATA_FORMAT_VERSION = 1


class MigrationStep(NamedTuple):
    """A single forward-only migration step.

    ``version`` is the data-format version this step advances the database
    TO -- it runs only when the currently-stamped version is less than
    this. ``apply`` receives the open connection, inside the single
    transaction run_migrations() wraps every pending step in, and performs
    whatever transform is needed. It must not commit, roll back, or close
    the connection, and must not use ``executescript()`` (which issues its
    own COMMIT first) -- either would break the all-or-nothing guarantee
    that a failed step leaves the database exactly as it was.
    """

    version: int
    description: str
    apply: "Callable[[sqlite3.Connection], None]"


def _step_1_baseline(conn: sqlite3.Connection) -> None:
    """Version 1: baseline stamp, no data transform.

    Registered as the extensibility pattern future migrations follow (see
    CURRENT_DATA_FORMAT_VERSION's docstring) -- the token DB schema and
    /data layout are unchanged from the pre-0.2.0 two-container topology,
    so there is nothing to actually migrate yet.
    """


# Ordered oldest-to-newest; run_migrations() applies every step whose
# version exceeds the database's current stamp, in this order.
_MIGRATIONS: tuple[MigrationStep, ...] = (
    MigrationStep(1, "baseline data-format version marker", _step_1_baseline),
)


def _check_current_version_matches_migrations(
    current_version: int, migrations: tuple[MigrationStep, ...]
) -> None:
    """Raise if CURRENT_DATA_FORMAT_VERSION and _MIGRATIONS have drifted apart.

    They both encode "the current data format" but nothing else ties them
    together -- a future change that bumps one without the other would let
    `migrate` silently stop stamping the version assert_post_update() (via
    --check) expects, turning into a confusing A5 failure downstream
    instead of a clear error right here. A standalone function (rather than
    inline module-level code) so it's independently testable with
    synthetic inputs, not just the real (currently-in-sync) module state.
    """
    last_step_version = migrations[-1].version if migrations else 0
    if last_step_version != current_version:
        raise AssertionError(
            f"CURRENT_DATA_FORMAT_VERSION ({current_version}) does not match "
            f"the last registered migration step's version ({last_step_version}) -- "
            "update one to match the other."
        )


# Checked at import time so a future drift is caught as soon as this
# module is loaded, not just when a migration happens to run.
_check_current_version_matches_migrations(CURRENT_DATA_FORMAT_VERSION, _MIGRATIONS)


def get_data_format_version(conn: sqlite3.Connection) -> int:
    """Read the data-format version stamped on this database.

    Uses SQLite's PRAGMA user_version: a plain integer in the database
    file's header, atomic with the rest of the file (no separate table or
    transaction needed). A database that has never been migrated reads 0.
    """
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else 0


def read_data_format_version(db_path: Path) -> int:
    """Read the data-format version of the database at ``db_path``.

    Unlike get_connection(), this opens a plain connection without
    setting journal_mode or touching the schema, so it is safe to call
    against a database this build may not understand (e.g. one stamped
    newer than CURRENT_DATA_FORMAT_VERSION).
    """
    conn = sqlite3.connect(db_path)
    try:
        return get_data_format_version(conn)
    finally:
        conn.close()


def _check_not_newer_than_supported(version: int) -> None:
    """Raise ValueError if ``version`` is newer than this build supports.

    For a forward-only marker, a newer-than-supported stamp is not
    "nothing to do": no step in _MIGRATIONS exceeds it, so applying
    migrations would silently no-op and report success. It almost always
    means a downgrade (an older magpie build running against data a newer
    build already migrated), which must fail closed rather than proceed
    against data this build doesn't understand.
    """
    if version > CURRENT_DATA_FORMAT_VERSION:
        raise ValueError(
            f"Database data-format version ({version}) is newer than this build of "
            f"magpie supports (current: {CURRENT_DATA_FORMAT_VERSION}). This usually "
            "means a downgrade -- an older magpie build running against data a newer "
            "build already migrated. Refusing to proceed; the database was not "
            f"modified. Run a newer magpie image that supports data-format version {version} "
            "or newer, or restore a backup taken before the upgrade."
        )


def _set_data_format_version(conn: sqlite3.Connection, version: int) -> None:
    """Set PRAGMA user_version.

    PRAGMA statements don't accept bound parameters, but ``version`` is
    always one of this module's own hardcoded MigrationStep.version values
    -- never operator- or network-controlled input -- so interpolating it
    directly is safe.
    """
    conn.execute(f"PRAGMA user_version = {int(version)}")


def run_migrations(conn: sqlite3.Connection) -> tuple[int, int, list[str]]:
    """Apply every pending migration step, in order, atomically.

    The version read, every pending step, and the new version stamp all
    run as one unit: any exception rolls all of it back, so the database is
    never left partially migrated. PRAGMA user_version lives in the
    database header, so the stamp commits or rolls back with the rest.

    If ``conn`` has no open transaction, this owns one: ``BEGIN IMMEDIATE``
    takes the write lock before the version is read, so concurrent callers
    against the same database serialize (the second one sees the first
    one's stamp and applies nothing), and it commits on success. If the
    caller already has a transaction open, the work runs in a savepoint
    inside it instead and the caller remains responsible for committing.

    Idempotent: running this against an already-current database applies
    nothing (version_before == version_after, applied == []).

    Args:
        conn: Open database connection.

    Returns:
        Tuple of (version_before, version_after, list of applied step
        descriptions, oldest first).

    Raises:
        ValueError: The database is already stamped with a data-format
            version newer than this build supports (see
            _check_not_newer_than_supported). Nothing is modified.
        Exception: Whatever a failing step raised, after rolling back.
    """
    owns_transaction = not conn.in_transaction
    conn.execute("BEGIN IMMEDIATE" if owns_transaction else "SAVEPOINT magpie_migrate")
    try:
        version_before = get_data_format_version(conn)
        _check_not_newer_than_supported(version_before)
        version = version_before
        applied: list[str] = []

        for step in _MIGRATIONS:
            # Checked against `version` (the last-applied step so far this
            # run), not the frozen `version_before` -- for a correctly
            # ascending-ordered _MIGRATIONS tuple the two are equivalent, but
            # checking the running value is the standard, defensive form: it
            # can't be fooled by a future _MIGRATIONS entry added out of
            # version order (a maintainer mistake this way just skips the
            # misplaced step instead of silently re-deriving "already past
            # it" from a stale baseline).
            if step.version <= version:
                continue
            step.apply(conn)
            version = step.version
            applied.append(f"v{step.version}: {step.description}")

        if applied:
            _set_data_format_version(conn, version)
        if owns_transaction:
            conn.commit()
        else:
            conn.execute("RELEASE SAVEPOINT magpie_migrate")
    except BaseException:
        if owns_transaction:
            conn.rollback()
        else:
            conn.execute("ROLLBACK TO SAVEPOINT magpie_migrate")
            conn.execute("RELEASE SAVEPOINT magpie_migrate")
        raise

    return version_before, version, applied


def migrate_database(db_path: Path) -> tuple[int, int, list[str]]:
    """Create (if needed) and migrate the database at ``db_path``.

    Everything that writes -- the newer-than-supported check, creating the
    tokens table, every pending step, and the stamp -- happens under one
    ``BEGIN IMMEDIATE`` write lock, taken before the version is read. A
    database a newer build stamps concurrently is therefore seen (and
    refused) before this build changes anything, and journal_mode is only
    switched to WAL after the stamp is known to be supported.

    Returns/raises as run_migrations().
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _check_not_newer_than_supported(get_data_format_version(conn))
            create_schema(conn)
            result = run_migrations(conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        conn.execute("PRAGMA journal_mode=WAL;")
    finally:
        conn.close()
    return result


@click.command()
@click.option(
    "--check",
    is_flag=True,
    help="Report the current data-format version without applying migrations.",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Print nothing when the data format is already current (errors and applied steps still print).",
)
@click.pass_obj
def migrate(ctx: CTLContext, check: bool, quiet: bool) -> None:
    """Apply pending data-format migrations.

    Stamps (and, for a future release that changes the schema/layout,
    transforms) the on-disk data format via a forward-only version marker
    (SQLite PRAGMA user_version on magpie.db). Safe to run repeatedly -- a
    database already at the current version is left untouched.

    Each run applies every pending step in a single transaction: a
    failure leaves the database exactly as it was. A database stamped
    newer than this build supports is refused without being modified.

    Run automatically by the bundled image on every container start,
    before it serves traffic; also run by `magpie-deploy.sh update` after
    the image swap, and safe to run manually.

    Use --check to report the current version without making any change --
    used by the installer's post-update assertion gate.

    Use --quiet to suppress the "already current" message (used by the
    container's startup path to keep routine restarts' logs quiet).

    Examples:

        magpie-ctl migrate

        magpie-ctl migrate --check
    """
    settings = ctx.settings

    if check:
        if not settings.database_path.exists():
            version = 0
        else:
            version = read_data_format_version(settings.database_path)

        if is_json_output():
            output_result(
                CommandResult(
                    data={
                        "data_format_version": version,
                        "current_data_format_version": CURRENT_DATA_FORMAT_VERSION,
                    },
                    human_output="",
                )
            )
            return
        click.echo(f"Data-format version: {version} (current: {CURRENT_DATA_FORMAT_VERSION})")
        return

    try:
        version_before, version_after, applied = migrate_database(settings.database_path)
    except ValueError as e:
        # A clean, structured error (both output formats) rather than a raw
        # traceback -- only raised for the newer-than-supported-stamp
        # (likely downgrade) case; see run_migrations()'s docstring.
        output_error(ErrorCode.CONFLICT, str(e))
    except sqlite3.Error as e:
        output_error(
            ErrorCode.SERVER_ERROR,
            f"Data-format migration of {settings.database_path} failed and was "
            f"rolled back; the database was left unchanged: {e}",
        )

    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "version_before": version_before,
                    "version_after": version_after,
                    "applied": applied,
                },
                human_output="",
            )
        )
        return

    if applied:
        click.echo(f"Migrated data format: v{version_before} -> v{version_after}")
        for step in applied:
            click.echo(f"  - {step}")
    elif not quiet:
        click.echo(f"Data-format already current (v{version_after}); nothing to do.")
