"""GC command for garbage collecting untagged blobs."""

from __future__ import annotations

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
    storage_path,
    retention_days: int,
    dry_run: bool,
    reconcile_only: bool,
    quiet: bool,
    debug: bool,
) -> tuple[GCResult, list[BlobToDelete]]:
    """Run GC with rich progress bars.

    This wraps run_gc() with progress display for CTL.

    Returns:
        Tuple of (GCResult, list of blobs to delete).
    """
    # We need to run in phases to show progress bars properly
    # Phase 1: Scan artifacts
    scan_manifest_files = list(storage_path.rglob(".magpie"))
    scan_total = len(scan_manifest_files)

    # Create progress tracking state
    scan_progress_ctx = None
    scan_task_id = None
    delete_progress_ctx = None
    delete_task_id = None

    # Use a two-pass approach:
    # Pass 1: Scan with progress bar
    with count_progress("Scanning artifacts", scan_total, quiet=quiet) as (progress, task_id):
        scan_progress_ctx = progress
        scan_task_id = task_id

        def scan_callback(phase: str, current: int, total: int) -> None:
            if phase == "scan" and scan_progress_ctx is not None and scan_task_id is not None:
                scan_progress_ctx.update(scan_task_id, completed=current)

        # Run scan phase only (dry_run=True to skip deletion in first pass)
        result, blobs_to_delete = run_gc(
            storage_path=storage_path,
            retention_days=retention_days,
            dry_run=True,  # Always dry-run in scan phase
            reconcile_only=reconcile_only,
            progress_callback=scan_callback,
        )

    # Pass 2: Delete blobs with progress bar (if not dry-run and not reconcile-only)
    if not dry_run and not reconcile_only and blobs_to_delete:
        with count_progress("Deleting blobs", len(blobs_to_delete), quiet=quiet) as (
            progress,
            task_id,
        ):
            delete_progress_ctx = progress
            delete_task_id = task_id

            for idx, blob in enumerate(blobs_to_delete):
                if debug:
                    click.echo(
                        f"  Deleting blob {blob.blob_hash[:12]}... ({blob.age_days} days old)",
                        err=True,
                    )

                blob.blob_file.unlink()

                # Also delete metadata sidecar if it exists
                if blob.metadata_file is not None:
                    blob.metadata_file.unlink()

                # Update progress
                if delete_progress_ctx is not None and delete_task_id is not None:
                    delete_progress_ctx.update(delete_task_id, advance=1)

        # Update result with actual deletion stats
        result.blobs_deleted = len(blobs_to_delete)
        result.space_reclaimed_bytes = sum(b.size for b in blobs_to_delete)

        # Re-run cleanup after actual deletion
        from magpie.storage.cleanup import cleanup_artifact_directories

        # Collect artifact dirs from blobs_to_delete
        artifact_dirs = set()
        for blob in blobs_to_delete:
            artifact_dirs.add(blob.blob_file.parent.parent)

        # Also need to cleanup artifacts that had only symlink fixes
        # Re-scan for all artifact dirs
        for manifest_file in storage_path.rglob(".magpie"):
            artifact_dirs.add(manifest_file.parent)

        # Run cleanup
        from magpie.storage.cleanup import CleanupStats

        combined_stats = CleanupStats()
        for artifact_dir in artifact_dirs:
            stats = cleanup_artifact_directories(artifact_dir, storage_path, dry_run=False)
            combined_stats.empty_blobs_dirs += stats.empty_blobs_dirs
            combined_stats.empty_metadata_dirs += stats.empty_metadata_dirs
            combined_stats.empty_manifests += stats.empty_manifests
            combined_stats.empty_artifact_dirs += stats.empty_artifact_dirs
            combined_stats.empty_parent_dirs += stats.empty_parent_dirs
            combined_stats.removed_paths.extend(stats.removed_paths)

        result.cleanup_stats = combined_stats
        result.items_removed = combined_stats.total_removed

    return result, blobs_to_delete


def _print_summary(result: GCResult, dry_run: bool, reconcile_only: bool, debug: bool) -> None:
    """Print GC summary to stdout."""
    click.echo("")
    click.echo("GC Summary:")
    click.echo(f"  Artifacts scanned: {result.artifacts_scanned}")
    click.echo(f"  Blobs found: {result.blobs_found}")

    # In dry run, blobs_deleted is the count that would be deleted (== untagged)
    click.echo(f"  Untagged blobs: {result.blobs_deleted}")

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
