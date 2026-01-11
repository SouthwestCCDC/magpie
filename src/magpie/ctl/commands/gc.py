"""GC command for garbage collecting untagged blobs."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from magpie.ctl import CTLContext
from magpie.storage.manifest import read_manifest
from magpie.storage.metadata import read_metadata
from magpie.storage.symlinks import reconcile_symlinks


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
    "--json",
    "json_output",
    is_flag=True,
    help="Output machine-readable JSON statistics.",
)
@click.option(
    "--quiet",
    is_flag=True,
    help="Suppress progress output (no effect with --json).",
)
@click.pass_obj
def gc(
    ctx: CTLContext, dry_run: bool, reconcile_only: bool, json_output: bool, quiet: bool
) -> None:
    """Garbage collect untagged blobs older than retention period.

    Walks all artifact directories and:
    - Identifies blobs not referenced by any tag
    - Deletes untagged blobs older than retention_days (default 90)
    - Reconciles symlinks to match manifests

    Use --dry-run to see what would be deleted without making changes.
    Use --reconcile-only to only fix symlinks without deleting blobs.
    Use --json to output machine-readable statistics.
    Use --quiet to suppress progress bars.

    Examples:

        magpie-ctl gc --dry-run

        magpie-ctl gc

        magpie-ctl gc --reconcile-only

        magpie-ctl gc --json
    """
    settings = ctx.settings
    storage_path = settings.storage_path
    retention_days = settings.retention_days

    if not storage_path.exists():
        raise click.ClickException(f"Storage path does not exist: {storage_path}")

    if ctx.debug and not json_output:
        click.echo(f"Storage path: {storage_path}", err=True)
        click.echo(f"Retention days: {retention_days}", err=True)

    # Track statistics
    total_artifacts = 0
    total_blobs_found = 0
    untagged_blobs = 0
    deleted_blobs = 0
    deleted_bytes = 0
    reconciled_artifacts = 0
    symlinks_checked = 0
    symlinks_fixed = 0

    now = datetime.now(timezone.utc)

    # Collect all manifest files first for progress tracking
    manifest_files = list(storage_path.rglob(".magpie"))

    # Determine if we should show progress
    show_progress = not json_output and not quiet and sys.stdout.isatty()

    # Process artifacts with optional progress bar
    artifacts_iter = manifest_files
    if show_progress:
        artifacts_iter = click.progressbar(
            manifest_files,
            label="Scanning artifacts",
            show_pos=True,
            item_show_func=lambda x: str(x.parent.relative_to(storage_path)) if x else "",
        )

    for manifest_file in artifacts_iter:
        artifact_dir = manifest_file.parent
        artifact_path = str(artifact_dir.relative_to(storage_path))
        total_artifacts += 1

        if ctx.debug and not json_output:
            click.echo(f"Processing artifact: {artifact_path}", err=True)

        # Read manifest to get tagged hashes
        # Manifest stores full hashes, but blobs are stored with short hashes (8 chars)
        manifest = read_manifest(artifact_dir)
        tagged_hashes = {h[:8] for h in manifest.tags.values()}

        # Reconcile symlinks for this artifact
        checked, fixed = reconcile_symlinks(artifact_dir, manifest)
        symlinks_checked += checked
        symlinks_fixed += fixed
        reconciled_artifacts += 1

        if reconcile_only:
            continue

        # Find all blobs in blobs/ directory
        blobs_dir = artifact_dir / "blobs"
        if not blobs_dir.exists():
            continue

        for blob_file in blobs_dir.iterdir():
            if not blob_file.is_file():
                continue

            total_blobs_found += 1
            blob_hash = blob_file.name

            # Check if blob is tagged
            if blob_hash in tagged_hashes:
                continue

            untagged_blobs += 1

            # Get blob age from metadata or file mtime
            blob_age_days = _get_blob_age_days(artifact_dir, blob_hash, now)

            if blob_age_days is None:
                # Can't determine age, skip
                if ctx.debug and not json_output:
                    click.echo(f"  Skipping blob {blob_hash[:12]}... (unknown age)", err=True)
                continue

            # Check if blob is older than retention period
            if blob_age_days < retention_days:
                if ctx.debug and not json_output:
                    click.echo(
                        f"  Keeping blob {blob_hash[:12]}... ({blob_age_days} days old < {retention_days})",
                        err=True,
                    )
                continue

            # Delete old untagged blob
            blob_size = blob_file.stat().st_size

            if dry_run:
                if not json_output:
                    click.echo(
                        f"Would delete: {artifact_path}/blobs/{blob_hash[:12]}... "
                        f"({blob_age_days} days old, {_format_size(blob_size)})"
                    )
            else:
                if ctx.debug and not json_output:
                    click.echo(
                        f"  Deleting blob {blob_hash[:12]}... ({blob_age_days} days old)",
                        err=True,
                    )
                blob_file.unlink()

                # Also delete metadata sidecar if it exists
                metadata_file = artifact_dir / "metadata" / f"{blob_hash}.json"
                if metadata_file.exists():
                    metadata_file.unlink()

            deleted_blobs += 1
            deleted_bytes += blob_size

    # Output results
    if json_output:
        # JSON output mode
        output = {
            "artifacts_scanned": total_artifacts,
            "blobs_deleted": deleted_blobs,
            "bytes_reclaimed": deleted_bytes,
            "symlinks_checked": symlinks_checked,
            "symlinks_fixed": symlinks_fixed,
            "dry_run": dry_run,
        }
        click.echo(json.dumps(output, indent=2))
    else:
        # Human-readable output mode
        click.echo("")
        click.echo("GC Summary:")
        click.echo(f"  Artifacts scanned: {total_artifacts}")
        click.echo(f"  Blobs found: {total_blobs_found}")
        click.echo(f"  Untagged blobs: {untagged_blobs}")
        click.echo(f"  Symlinks reconciled: {reconciled_artifacts} artifact(s)")
        click.echo(f"  Symlinks checked: {symlinks_checked}")
        click.echo(f"  Symlinks fixed: {symlinks_fixed}")

        if not reconcile_only:
            action = "Would delete" if dry_run else "Deleted"
            click.echo(f"  {action}: {deleted_blobs} blob(s), {_format_size(deleted_bytes)}")


def _get_blob_age_days(artifact_dir: Path, blob_hash: str, now: datetime) -> int | None:
    """Get blob age in days from metadata or file mtime.

    Args:
        artifact_dir: Path to artifact directory.
        blob_hash: Full blob hash.
        now: Current datetime for comparison.

    Returns:
        Age in days, or None if age cannot be determined.
    """
    try:
        # Try to get age from metadata
        metadata = read_metadata(artifact_dir, blob_hash)
        upload_time = metadata.uploaded_at
        if upload_time.tzinfo is None:
            upload_time = upload_time.replace(tzinfo=timezone.utc)
        age = now - upload_time
        return age.days
    except Exception:
        # Fall back to file mtime
        blob_file = artifact_dir / "blobs" / blob_hash
        if blob_file.exists():
            mtime = datetime.fromtimestamp(blob_file.stat().st_mtime, tz=timezone.utc)
            age = now - mtime
            return age.days
        return None


def _format_size(size_bytes: int) -> str:
    """Format byte size as human-readable string.

    Args:
        size_bytes: Size in bytes.

    Returns:
        Human-readable size string (e.g., "1.5 MB").
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"
