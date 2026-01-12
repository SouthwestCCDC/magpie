"""GC command for garbage collecting untagged blobs."""

from __future__ import annotations

from pathlib import Path

import click

from magpie.cli.progress import count_progress
from magpie.ctl import CTLContext
from magpie.storage.gc import BlobToDelete, GCResult, format_size, run_gc


@click.command()
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be deleted without actually deleting.",
)
@click.option(
    "--reconcile-only",
    is_flag=True,
    help="Only reconcile symlinks, don't delete blobs.",
)
@click.option(
    "--retention-days",
    "retention_days_flag",
    type=click.IntRange(min=0),
    default=None,
    help="Override retention period (days). Defaults to config value.",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress progress output.",
)
@click.pass_obj
def gc(
    ctx: CTLContext,
    dry_run: bool,
    reconcile_only: bool,
    retention_days_flag: int | None,
    quiet: bool,
) -> None:
    """Garbage collect untagged blobs older than retention period.

    Walks all artifact directories and:
    - Identifies blobs not referenced by any tag
    - Deletes untagged blobs older than retention_days (default 90)
    - Reconciles symlinks to match manifests

    Use --dry-run to see what would be deleted without making changes.
    Use --reconcile-only to only fix symlinks without deleting blobs.
    Use --retention-days to override the configured retention period.
    Use --quiet to suppress progress bars.

    Examples:

        magpie-ctl gc --dry-run

        magpie-ctl gc

        magpie-ctl gc --reconcile-only

        magpie-ctl gc --retention-days 0

        magpie-ctl gc --retention-days 7

        magpie-ctl gc --quiet
    """
    settings = ctx.settings
    storage_path = settings.storage_path
    # Use CLI argument if provided, otherwise fall back to config
    retention_days = (
        retention_days_flag if retention_days_flag is not None else settings.retention_days
    )

    if not storage_path.exists():
        raise click.ClickException(f"Storage path does not exist: {storage_path}")

    if ctx.debug:
        click.echo(f"Storage path: {storage_path}", err=True)
        click.echo(f"Retention days: {retention_days}", err=True)

    # Run GC with progress display using a two-phase approach
    # Phase 1: Scan (with progress bar)
    # Phase 2: Delete (with progress bar)
    result, blobs_to_delete = _run_gc_with_progress(
        storage_path=storage_path,
        retention_days=retention_days,
        dry_run=dry_run,
        reconcile_only=reconcile_only,
        quiet=quiet,
        debug=ctx.debug,
    )

    # Print dry-run deletion preview
    if dry_run and not reconcile_only and blobs_to_delete:
        for blob in blobs_to_delete:
            click.echo(
                f"Would delete: {blob.artifact_path}/blobs/{blob.blob_hash[:12]}... "
                f"({blob.age_days} days old, {format_size(blob.size)})"
            )

    # Print dry-run directory removal preview
    if dry_run and not reconcile_only and result.cleanup_stats.removed_paths:
        for path in result.cleanup_stats.removed_paths:
            click.echo(f"Would remove: {path}")

    # Print summary
    _print_summary(result, dry_run, reconcile_only, ctx.debug)


def _run_gc_with_progress(
    storage_path: Path,
    retention_days: int,
    dry_run: bool,
    reconcile_only: bool,
    quiet: bool,
    debug: bool,
) -> tuple[GCResult, list[BlobToDelete]]:
    """Run GC with rich progress bars.

    This wraps run_gc() with progress display for CTL. The shared run_gc()
    function handles all the actual work (scanning, deletion, cleanup) while
    this wrapper manages progress bar display via the callback mechanism.

    Uses a single-pass approach for actual GC work. The delete progress bar
    total is updated dynamically when the first delete callback arrives.

    Args:
        storage_path: Base storage path containing artifacts.
        retention_days: Delete untagged blobs older than this many days.
        dry_run: If True, preview what would be deleted without making changes.
        reconcile_only: If True, only reconcile symlinks, don't delete blobs.
        quiet: If True, suppress progress output.
        debug: If True, output debug messages.

    Returns:
        Tuple of (GCResult, list of blobs to delete).
    """
    # Pre-scan to get artifact count for progress bar
    scan_manifest_files = list(storage_path.rglob(".magpie"))
    scan_total = len(scan_manifest_files)

    # Track progress state across phases
    # We use a class to allow the nested callback to update state
    class ProgressState:
        scan_progress: object = None
        scan_task_id: object = None
        delete_progress: object = None
        delete_task_id: object = None
        delete_total_set: bool = False

    state = ProgressState()

    def progress_callback(phase: str, current: int, total: int) -> None:
        """Handle progress updates from run_gc()."""
        if phase == "scan" and state.scan_progress is not None and state.scan_task_id is not None:
            state.scan_progress.update(state.scan_task_id, completed=current)
        elif phase == "delete":
            # First delete callback tells us the total, update progress bar
            if not state.delete_total_set and total > 0:
                state.delete_total_set = True
                if state.delete_progress is not None and state.delete_task_id is not None:
                    state.delete_progress.update(state.delete_task_id, total=total)

            if state.delete_progress is not None and state.delete_task_id is not None:
                state.delete_progress.update(state.delete_task_id, completed=current)

    # For dry-run or reconcile-only, we just need the scan progress bar
    if dry_run or reconcile_only:
        with count_progress("Scanning artifacts", scan_total, quiet=quiet) as (progress, task_id):
            state.scan_progress = progress
            state.scan_task_id = task_id

            result, blobs_to_delete = run_gc(
                storage_path=storage_path,
                retention_days=retention_days,
                dry_run=dry_run,
                reconcile_only=reconcile_only,
                progress_callback=progress_callback,
            )
        return result, blobs_to_delete

    # For actual deletion, we run run_gc once with both progress bars active.
    # The scan progress bar shows artifact scanning progress.
    # The delete progress bar starts with total=0 and is updated dynamically
    # when the first delete callback tells us how many blobs there are.
    #
    # Note: We could show sequential progress bars (scan, then delete), but that
    # would require either running GC twice (once for preview, once for actual)
    # or restructuring run_gc to yield between phases. For simplicity, we show
    # both bars and update them as callbacks arrive.
    with count_progress("Scanning artifacts", scan_total, quiet=quiet) as (
        scan_prog,
        scan_tid,
    ):
        state.scan_progress = scan_prog
        state.scan_task_id = scan_tid

        # Run GC - callbacks will update both progress bars as appropriate
        result, blobs_to_delete = run_gc(
            storage_path=storage_path,
            retention_days=retention_days,
            dry_run=False,
            reconcile_only=False,
            progress_callback=progress_callback,
        )

    return result, blobs_to_delete


def _print_summary(result: GCResult, dry_run: bool, reconcile_only: bool, debug: bool) -> None:
    """Print GC summary to stdout."""
    click.echo("")
    click.echo("GC Summary:")
    click.echo(f"  Artifacts scanned: {result.artifacts_scanned}")
    click.echo(f"  Blobs found: {result.blobs_found}")

    # blobs_deleted represents untagged blobs that are old enough to be deleted
    # (older than retention_days), not all untagged blobs
    click.echo(f"  Untagged blobs (eligible for deletion): {result.blobs_deleted}")

    click.echo(f"  Symlinks checked: {result.symlinks_checked}")

    # Display symlinks fixed with optional details
    if result.symlinks_fixed == 0:
        click.echo(f"  Symlinks fixed: {result.symlinks_fixed}")
    else:
        details_str = "; ".join(str(d) for d in result.symlink_fix_details)
        click.echo(f"  Symlinks fixed: {result.symlinks_fixed} ({details_str})")

    if not reconcile_only:
        action = "Would delete" if dry_run else "Deleted"
        click.echo(
            f"  {action}: {result.blobs_deleted} blob(s), {format_size(result.space_reclaimed_bytes)}"
        )

        # Report directory cleanup
        if result.items_removed > 0:
            action = "Would remove" if dry_run else "Removed"
            click.echo(f"  {action} empty items: {result.items_removed}")
            if debug:
                stats = result.cleanup_stats
                if stats.empty_blobs_dirs:
                    click.echo(f"    - blobs/ dirs: {stats.empty_blobs_dirs}", err=True)
                if stats.empty_metadata_dirs:
                    click.echo(f"    - metadata/ dirs: {stats.empty_metadata_dirs}", err=True)
                if stats.empty_manifests:
                    click.echo(f"    - .magpie files: {stats.empty_manifests}", err=True)
                if stats.empty_artifact_dirs:
                    click.echo(f"    - artifact dirs: {stats.empty_artifact_dirs}", err=True)
                if stats.empty_parent_dirs:
                    click.echo(f"    - parent dirs: {stats.empty_parent_dirs}", err=True)
