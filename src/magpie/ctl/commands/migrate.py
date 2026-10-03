"""Migrate command for the data-format version marker (issue #561).

Provides the migration mechanism the bundled image's wrapper.sh runs on
every container start (before serving traffic, under the init lock) and
`magpie-deploy.sh update` also calls after swapping in a new image, so a
future release that actually
changes the token DB schema or the on-disk /data layout has somewhere to
register a transform. As of v0.2.0 there is nothing to transform (the
bundled single-container image reads the same /data layout the pre-0.2.0
two-container topology used), so this only stamps a baseline version.

Startup only ever migrates forward. Going back to an older build is an
explicit operator step (issue #646): `magpie-ctl migrate --to N`, run with
the newer build (the only one that knows how to undo its own steps),
reverts the database to data-format version N before the older build is
started.
"""

from __future__ import annotations

import contextlib
import os
import re
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple, TypeVar

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
    """A single migration step.

    ``version`` is the data-format version this step advances the database
    TO -- it runs only when the currently-stamped version is less than
    this. ``apply`` receives the open connection, inside the single
    transaction run_migrations() wraps every pending step in, and performs
    whatever transform is needed. ``revert`` undoes exactly what ``apply``
    did, taking a version-``version`` database back to version
    ``version - 1``'s schema and data; revert_database() runs it.

    Every step must either define ``revert`` or set ``irreversible=True``
    (never both): a step that can't be undone says so explicitly, and
    `migrate --to` refuses to cross it (restoring a backup is then the way
    back). check_migrations_reversibility() enforces this.

    Neither callable may commit, roll back, or close the connection, or
    use ``executescript()`` (which issues its own COMMIT first) -- either
    would break the all-or-nothing guarantee that a failed run leaves the
    database exactly as it was. Neither writes the version stamp; the
    caller stamps once, after every step has succeeded.
    """

    version: int
    description: str
    apply: "Callable[[sqlite3.Connection], None]"
    revert: "Callable[[sqlite3.Connection], None] | None" = None
    irreversible: bool = False


def _step_1_baseline(conn: sqlite3.Connection) -> None:
    """Version 1: baseline stamp, no data transform.

    Registered as the extensibility pattern future migrations follow (see
    CURRENT_DATA_FORMAT_VERSION's docstring) -- the token DB schema and
    /data layout are unchanged from the pre-0.2.0 two-container topology,
    so there is nothing to actually migrate yet.
    """


def _revert_step_1_baseline(conn: sqlite3.Connection) -> None:
    """Undo version 1: nothing to transform; the stamp drops back to 0."""


# Ordered oldest-to-newest; run_migrations() applies every step whose
# version exceeds the database's current stamp, in this order, and
# revert_database() reverts them newest-first.
_MIGRATIONS: tuple[MigrationStep, ...] = (
    MigrationStep(
        1,
        "baseline data-format version marker",
        _step_1_baseline,
        revert=_revert_step_1_baseline,
    ),
)


def check_migrations_reversibility(migrations: tuple[MigrationStep, ...]) -> None:
    """Raise AssertionError unless every step has exactly one of ``revert`` or ``irreversible``."""
    problems = []
    for step in migrations:
        if step.revert is None and not step.irreversible:
            problems.append(
                f"v{step.version} ({step.description}) has no revert; "
                "add one or mark it irreversible=True"
            )
        elif step.revert is not None and step.irreversible:
            problems.append(
                f"v{step.version} ({step.description}) has a revert but is marked irreversible"
            )
    if problems:
        raise AssertionError("Invalid migration steps: " + "; ".join(problems))


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
            "or newer. To keep using this build instead, first revert the data with that "
            f"newer image (`magpie-ctl migrate --to {CURRENT_DATA_FORMAT_VERSION}`), or "
            "restore a backup taken before the upgrade."
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
    inside it instead (still under the write lock, which fails if the
    caller's read snapshot is out of date) and the caller remains
    responsible for committing.

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
        if not owns_transaction:
            # Rewriting the stamp unchanged takes the write lock inside the
            # caller's transaction. SQLite refuses that (SQLITE_BUSY_SNAPSHOT)
            # if the caller's read snapshot is stale, so the version
            # _apply_pending_steps() reads is the latest committed one.
            _set_data_format_version(conn, get_data_format_version(conn))
        result = _apply_pending_steps(conn)
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

    return result


def _apply_pending_steps(conn: sqlite3.Connection) -> tuple[int, int, list[str]]:
    """Check the stamp, apply pending steps, and stamp the result.

    The caller must already hold the write lock and owns commit/rollback.
    Writes nothing when the database is already current.
    """
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
    return version_before, version, applied


# Before a migration or revert changes the database, a copy of it is kept
# in this directory next to it (/data/backups). That copy is the way back
# from a step that turns out to be wrong, which `migrate --to` can't undo,
# for deployments that don't go through the installer's own pre-update
# backup. It is on the same volume, so it is not disaster recovery.
SNAPSHOT_DIR_NAME = "backups"
SNAPSHOTS_TO_KEEP = 3
_PARTIAL_SUFFIX = ".partial"
# Only names snapshot_database() writes are counted and pruned; anything an
# operator puts in the directory (e.g. magpie.db.manual) is left alone.
_SNAPSHOT_NAME_TAIL = re.compile(r"\.\d{8}T\d{12}Z\.pre-(?:revert-to-)?v\d+(?:\.partial)?")
# A concurrent migrate/revert can change the version between the copy and
# the write lock; the copy is then retaken. Bounded so a database that keeps
# changing fails instead of looping.
_SNAPSHOT_ATTEMPTS = 3

_T = TypeVar("_T")


class SnapshotError(Exception):
    """The pre-migration copy of the database could not be written."""


def snapshot_database(db_path: Path, reason: str) -> Path:
    """Copy the database at ``db_path`` into its backups directory.

    Uses SQLite's online backup, so the copy is consistent even while the
    server has the database open. The copy is named
    ``<db name>.<UTC timestamp>.<reason>`` (sortable by time), is only
    readable by this uid (it holds the token hashes). Old copies are only
    pruned (prune_snapshots()) once the change it guards has committed.

    Raises:
        SnapshotError: The copy could not be written. Nothing was changed
            except, possibly, creating the backups directory.
    """
    backup_dir = db_path.parent / SNAPSHOT_DIR_NAME
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    dest = backup_dir / f"{db_path.name}.{timestamp}.{reason}"
    partial = dest.with_name(dest.name + _PARTIAL_SUFFIX)
    try:
        backup_dir.mkdir(mode=0o700, exist_ok=True)
        backup_dir.chmod(0o700)
        os.close(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        src = sqlite3.connect(db_path)
        try:
            dst = sqlite3.connect(partial)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        partial.replace(dest)
    except (OSError, sqlite3.Error) as e:
        with contextlib.suppress(OSError):
            partial.unlink(missing_ok=True)
        raise SnapshotError(
            f"Could not save a copy of {db_path} to {backup_dir} before changing it, "
            f"so it was not changed: {e}. Free up space on the data volume (old "
            f"copies in {backup_dir} can be deleted) and try again."
        ) from e
    return dest


def prune_snapshots(db_path: Path) -> None:
    """Keep only the newest SNAPSHOTS_TO_KEEP copies; drop leftover partials.

    Best-effort: the change the copies guard has already committed, so a
    failure here must not turn it into an error.
    """
    backup_dir = db_path.parent / SNAPSHOT_DIR_NAME
    prefix = re.escape(db_path.name)
    try:
        copies = sorted(
            c
            for c in backup_dir.iterdir()
            if re.fullmatch(prefix + _SNAPSHOT_NAME_TAIL.pattern, c.name)
        )
    except OSError:
        return
    complete = [c for c in copies if not c.name.endswith(_PARTIAL_SUFFIX)]
    stale = [c for c in copies if c.name.endswith(_PARTIAL_SUFFIX)]
    stale += complete[:-SNAPSHOTS_TO_KEEP]
    for path in stale:
        with contextlib.suppress(OSError):
            path.unlink()


def _discard_snapshot(snapshot: Path | None) -> None:
    if snapshot is not None:
        with contextlib.suppress(OSError):
            snapshot.unlink()


def _db_state(conn: sqlite3.Connection) -> tuple[int, bool]:
    """(data-format version, whether the database has any tables)."""
    has_tables = conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone() is not None
    return get_data_format_version(conn), has_tables


def _change_with_snapshot(
    db_path: Path,
    snapshot_reason: Callable[[int], str | None],
    change: Callable[[sqlite3.Connection], _T],
    after_commit: Callable[[sqlite3.Connection, _T], None] | None = None,
) -> tuple[_T, Path | None]:
    """Run ``change`` in one BEGIN IMMEDIATE transaction, copying the DB first.

    ``snapshot_reason(version)`` names the copy to take before a database at
    ``version`` is changed, or returns None if it won't be. The online backup
    can't run while this connection holds the write lock, so the copy is
    taken first and the decision is re-checked under the lock: if another
    migrate/revert changed the version in between, the copy (which no
    longer matches) is discarded and the whole thing retried. An empty
    database (fresh volume) is never copied.

    If ``change`` fails, its copy is discarded too (the database is
    unchanged, so it is redundant) and nothing is pruned, so retries can't
    evict older copies. After a commit, old copies are pruned, then
    ``after_commit(conn, result)`` runs.

    Raises:
        SnapshotError: The copy failed, or the database kept changing under
            us; the database was not changed.
    """
    for _ in range(_SNAPSHOT_ATTEMPTS):
        snapshot = copied_version = None
        if db_path.is_file():
            conn = sqlite3.connect(db_path)
            try:
                copied_version, has_tables = _db_state(conn)
            finally:
                conn.close()
            reason = snapshot_reason(copied_version) if has_tables else None
            if reason:
                snapshot = snapshot_database(db_path, reason)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                version, has_tables = _db_state(conn)
                needs_copy = has_tables and snapshot_reason(version) is not None
                if needs_copy and (snapshot is None or version != copied_version):
                    conn.rollback()
                    _discard_snapshot(snapshot)
                    continue
                result = change(conn)
                conn.commit()
            except BaseException:
                conn.rollback()
                _discard_snapshot(snapshot)
                raise
            if snapshot is not None:
                prune_snapshots(db_path)
            if after_commit is not None:
                after_commit(conn, result)
            return result, snapshot
        finally:
            conn.close()
    raise SnapshotError(
        f"{db_path} kept changing while a copy of it was being saved (another "
        "migrate or migrate --to running?), so it was not changed. Try again."
    )


class JournalModeError(Exception):
    """The migration committed, but switching the database to WAL failed."""


def migrate_database(db_path: Path) -> tuple[int, int, list[str], Path | None]:
    """Create (if needed) and migrate the database at ``db_path``.

    Everything that writes -- the newer-than-supported check, creating the
    tokens table, every pending step, and the stamp -- happens under one
    ``BEGIN IMMEDIATE`` write lock, taken before the version is read. A
    database a newer build stamps concurrently is therefore seen (and
    refused) before this build changes anything. An already-current
    database is not written to. journal_mode is switched to WAL only after
    the migration has committed.

    An existing database with pending steps is first copied with
    snapshot_database(); that copy is taken before the write lock (the
    online backup can't run inside it), so a concurrent migrator can at
    worst leave an unneeded copy behind.

    Returns run_migrations()'s tuple plus the path of that copy (None if
    none was taken), and raises as run_migrations(), plus:

    Raises:
        SnapshotError: The copy failed; the database was not changed.
        JournalModeError: The migration committed but the switch to WAL
            failed (e.g. another process held the database open past the
            busy timeout). Re-running is safe: the migration is a no-op.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    def snapshot_reason(version: int) -> str | None:
        if version < CURRENT_DATA_FORMAT_VERSION:
            return f"pre-v{CURRENT_DATA_FORMAT_VERSION}"
        return None

    def change(conn: sqlite3.Connection) -> tuple[int, int, list[str]]:
        _check_not_newer_than_supported(get_data_format_version(conn))
        create_schema(conn)
        return _apply_pending_steps(conn)

    def switch_to_wal(conn: sqlite3.Connection, result: tuple[int, int, list[str]]) -> None:
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except sqlite3.OperationalError as e:
            raise JournalModeError(
                f"Data-format migration of {db_path} committed (now v{result[1]}), but "
                f"switching the database to WAL mode failed: {e}. Another process may "
                "be holding the database open; re-run once it is released."
            ) from e

    result, snapshot = _change_with_snapshot(db_path, snapshot_reason, change, switch_to_wal)
    return (*result, snapshot)


class MigrationTargetError(ValueError):
    """The requested `migrate --to` target version is invalid."""


def _revert_steps(conn: sqlite3.Connection, target: int) -> tuple[int, int, list[str]]:
    """Revert applied steps newest-first down to ``target`` and stamp it.

    The caller must already hold the write lock and owns commit/rollback.
    Every refusal is raised before any step runs. Writes nothing when the
    database is already at ``target``.
    """
    version_before = get_data_format_version(conn)
    _check_not_newer_than_supported(version_before)
    known_versions = {0} | {step.version for step in _MIGRATIONS}
    if target not in known_versions:
        raise MigrationTargetError(
            f"Data-format version {target} is not one this build of magpie knows "
            f"(known: {', '.join(str(v) for v in sorted(known_versions))})."
        )
    if target > version_before:
        raise MigrationTargetError(
            f"Cannot migrate --to {target}: the database is at data-format version "
            f"{version_before}, and --to only moves down. Run `magpie-ctl migrate` "
            "to move forward."
        )

    to_revert = [step for step in reversed(_MIGRATIONS) if target < step.version <= version_before]
    blocked = [step for step in to_revert if step.revert is None]
    if blocked:
        steps = ", ".join(f"v{step.version} ({step.description})" for step in blocked)
        raise ValueError(
            f"Cannot revert the data format from v{version_before} to v{target}: "
            f"irreversible step(s) in the way: {steps}. The database was not "
            "modified. Restore a backup taken before the upgrade instead."
        )

    reverted: list[str] = []
    for step in to_revert:
        if step.revert is not None:
            step.revert(conn)
            reverted.append(f"v{step.version}: {step.description}")

    if reverted:
        _set_data_format_version(conn, target)
    return version_before, target, reverted


def revert_database(db_path: Path, target: int) -> tuple[int, int, list[str], Path | None]:
    """Revert the database at ``db_path`` down to data-format version ``target``.

    Takes the same ``BEGIN IMMEDIATE`` write lock as migrate_database(),
    before the version is read, and runs every revert plus the final stamp
    in that one transaction: a failure leaves the database exactly as it
    was.

    An existing database above ``target`` is first copied with
    snapshot_database(), as in migrate_database().

    Returns:
        Tuple of (version_before, version_after, list of reverted step
        descriptions, newest first, path of that copy or None).

    Raises:
        FileNotFoundError: There is no database at ``db_path``.
        MigrationTargetError: ``target`` is not a known version, or is
            above the database's current version.
        ValueError: The database is newer than this build supports, or an
            irreversible step lies between it and ``target``.
        SnapshotError: The copy failed; the database was not changed.
        sqlite3.Error / Exception: Whatever failed, after rolling back.
    """
    if not db_path.is_file():
        raise FileNotFoundError(f"No database at {db_path}; nothing to revert.")
    known_targets = {0} | {step.version for step in _MIGRATIONS}

    def snapshot_reason(version: int) -> str | None:
        if target < version <= CURRENT_DATA_FORMAT_VERSION and target in known_targets:
            return f"pre-revert-to-v{target}"
        return None

    result, snapshot = _change_with_snapshot(
        db_path, snapshot_reason, lambda conn: _revert_steps(conn, target)
    )
    return (*result, snapshot)


@click.command()
@click.option(
    "--check",
    is_flag=True,
    help="Report the current data-format version without applying migrations.",
)
@click.option(
    "--to",
    "to_version",
    type=click.IntRange(min=0),
    default=None,
    metavar="N",
    help="Revert the data format down to version N (run with the newer image before switching to an older one).",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Print nothing when the data format is already current (errors and applied steps still print).",
)
@click.pass_obj
def migrate(ctx: CTLContext, check: bool, to_version: int | None, quiet: bool) -> None:
    """Apply pending data-format migrations, or revert them with --to.

    Stamps (and, for a future release that changes the schema/layout,
    transforms) the on-disk data format via a version marker (SQLite
    PRAGMA user_version on magpie.db). Safe to run repeatedly -- a
    database already at the current version is left untouched.

    Each run applies every pending step in a single transaction: a
    failure leaves the database exactly as it was. A database stamped
    newer than this build supports is refused without being modified.

    Run automatically by the bundled image on every container start,
    before it serves traffic; also run by `magpie-deploy.sh update` after
    the image swap, and safe to run manually.

    Use --check to report the current version without making any change --
    used by the installer's post-update assertion gate.

    Use --to N to downgrade: revert every applied step above version N,
    newest first, in one transaction under the same write lock, stamping N
    last. Run it with the NEWER image before switching to an older one
    whose supported version is N. Refused (database unchanged) if N is
    above the current version or unknown, or if a step in the way is
    irreversible. Container startup never does this on its own.

    Use --quiet to suppress the "already current" message (used by the
    container's startup path to keep routine restarts' logs quiet).

    Examples:

        magpie-ctl migrate

        magpie-ctl migrate --check

        magpie-ctl migrate --to 0
    """
    settings = ctx.settings

    if check and to_version is not None:
        raise click.UsageError("--check and --to cannot be used together.")
    if to_version is not None:
        _revert_command(settings.database_path, to_version, quiet)
        return

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
        version_before, version_after, applied, snapshot = migrate_database(settings.database_path)
    except SnapshotError as e:
        output_error(ErrorCode.SERVER_ERROR, str(e))
    except ValueError as e:
        # A clean, structured error (both output formats) rather than a raw
        # traceback -- only raised for the newer-than-supported-stamp
        # (likely downgrade) case; see run_migrations()'s docstring.
        output_error(ErrorCode.CONFLICT, str(e))
    except JournalModeError as e:
        output_error(ErrorCode.SERVER_ERROR, str(e))
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
                    "snapshot": str(snapshot) if snapshot else None,
                },
                human_output="",
            )
        )
        return

    if snapshot:
        click.echo(f"Saved a copy of the database first: {snapshot}")
    if applied:
        click.echo(f"Migrated data format: v{version_before} -> v{version_after}")
        for step in applied:
            click.echo(f"  - {step}")
    elif not quiet:
        click.echo(f"Data-format already current (v{version_after}); nothing to do.")


def _revert_command(db_path: Path, target: int, quiet: bool) -> None:
    """Run `migrate --to` and report the result."""
    try:
        version_before, version_after, reverted, snapshot = revert_database(db_path, target)
    except SnapshotError as e:
        output_error(ErrorCode.SERVER_ERROR, str(e))
    except FileNotFoundError as e:
        output_error(ErrorCode.NOT_FOUND, str(e))
    except MigrationTargetError as e:
        output_error(ErrorCode.VALIDATION_ERROR, str(e))
    except ValueError as e:
        output_error(ErrorCode.CONFLICT, str(e))
    except sqlite3.Error as e:
        output_error(
            ErrorCode.SERVER_ERROR,
            f"Data-format revert of {db_path} failed and was rolled back; the "
            f"database was left unchanged: {e}",
        )

    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "version_before": version_before,
                    "version_after": version_after,
                    "reverted": reverted,
                    "snapshot": str(snapshot) if snapshot else None,
                },
                human_output="",
            )
        )
        return

    if snapshot:
        click.echo(f"Saved a copy of the database first: {snapshot}")
    if reverted:
        click.echo(f"Reverted data format: v{version_before} -> v{version_after}")
        for step in reverted:
            click.echo(f"  - {step}")
    elif not quiet:
        click.echo(f"Data format already at v{version_after}; nothing to revert.")
