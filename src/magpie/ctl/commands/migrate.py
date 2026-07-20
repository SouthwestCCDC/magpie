"""Migrate command for the data-format version marker (issue #561).

Provides the forward-only migration mechanism `magpie-deploy.sh update`
calls after swapping in a new image, so a future release that actually
changes the token DB schema or the on-disk /data layout has somewhere to
register a transform. As of v0.2.0 there is nothing to transform (the
bundled single-container image reads the same /data layout the pre-0.2.0
two-container topology used), so this only stamps a baseline version.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, NamedTuple

import click

from magpie.auth.database import get_connection, init_database
from magpie.cli.formatting import CommandResult, is_json_output, output_result
from magpie.ctl import CTLContext

if TYPE_CHECKING:
    from collections.abc import Callable

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
    this. ``apply`` receives the open connection and performs whatever
    transform is needed; it must not commit or close the connection (
    run_migrations() does both once, after every applicable step has run).
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


def get_data_format_version(conn: sqlite3.Connection) -> int:
    """Read the data-format version stamped on this database.

    Uses SQLite's PRAGMA user_version: a plain integer in the database
    file's header, atomic with the rest of the file (no separate table or
    transaction needed). A database that has never been migrated reads 0.
    """
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else 0


def _set_data_format_version(conn: sqlite3.Connection, version: int) -> None:
    """Set PRAGMA user_version.

    PRAGMA statements don't accept bound parameters, but ``version`` is
    always one of this module's own hardcoded MigrationStep.version values
    -- never operator- or network-controlled input -- so interpolating it
    directly is safe.
    """
    conn.execute(f"PRAGMA user_version = {int(version)}")


def run_migrations(conn: sqlite3.Connection) -> tuple[int, int, list[str]]:
    """Apply every pending migration step, in order.

    Idempotent: running this against an already-current database applies
    nothing (version_before == version_after, applied == []).

    Args:
        conn: Open database connection. Not committed or closed by this
            function's caller-facing contract if no steps were applied.

    Returns:
        Tuple of (version_before, version_after, list of applied step
        descriptions, oldest first).
    """
    version_before = get_data_format_version(conn)
    version = version_before
    applied: list[str] = []

    for step in _MIGRATIONS:
        if step.version <= version_before:
            continue
        step.apply(conn)
        version = step.version
        applied.append(f"v{step.version}: {step.description}")

    if applied:
        _set_data_format_version(conn, version)
        conn.commit()

    return version_before, version, applied


@click.command()
@click.option(
    "--check",
    is_flag=True,
    help="Report the current data-format version without applying migrations.",
)
@click.pass_obj
def migrate(ctx: CTLContext, check: bool) -> None:
    """Apply pending data-format migrations.

    Stamps (and, for a future release that changes the schema/layout,
    transforms) the on-disk data format via a forward-only version marker
    (SQLite PRAGMA user_version on magpie.db). Safe to run repeatedly -- a
    database already at the current version is left untouched.

    Called automatically by `magpie-deploy.sh update` after the image
    swap; also safe to run manually.

    Use --check to report the current version without making any change --
    used by the installer's post-update assertion gate.

    Examples:

        magpie-ctl migrate

        magpie-ctl migrate --check
    """
    settings = ctx.settings

    if check:
        if not settings.database_path.exists():
            version = 0
        else:
            conn = get_connection(settings.database_path)
            try:
                version = get_data_format_version(conn)
            finally:
                conn.close()

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

    # init_database() is itself idempotent (CREATE TABLE IF NOT EXISTS) --
    # safe to call unconditionally so 'migrate' works standalone even
    # against a database that doesn't exist yet (e.g. run ahead of `init`).
    init_database(settings.database_path)

    conn = get_connection(settings.database_path)
    try:
        version_before, version_after, applied = run_migrations(conn)
    finally:
        conn.close()

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
    else:
        click.echo(f"Data format already current (v{version_after}); nothing to do.")
