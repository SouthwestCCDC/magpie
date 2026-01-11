"""GC command for garbage collecting untagged blobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import click

from magpie.cli.progress import count_progress
from magpie.ctl import CTLContext
from magpie.storage.manifest import read_manifest
from magpie.storage.metadata import read_metadata
from magpie.storage.symlinks import reconcile_symlinks


@dataclass
class BlobToDelete:
    """Information about a blob scheduled for deletion."""

    blob_file: Path
    artifact_path: str
    blob_hash: str
    age_days: int
    size: int
    metadata_file: Path | None


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

    # Track statistics
    total_artifacts = 0
    total_blobs_found = 0
    untagged_blobs = 0
    deleted_bytes = 0
    symlinks_checked = 0
    symlinks_fixed = 0
    symlinks_fixed_details: list[str] = []

    now = datetime.now(timezone.utc)

    # Pre-scan to count artifacts for progress bar
    manifest_files = list(storage_path.rglob(".magpie"))
    total_manifest_count = len(manifest_files)

    # Collect blobs to delete (for progress bar during deletion phase)
    blobs_to_delete: list[BlobToDelete] = []

    # Phase 1: Scan artifacts
    with count_progress("Scanning artifacts", total_manifest_count, quiet=quiet) as (
        progress,
        task_id,
    ):
        for manifest_file in manifest_files:
            artifact_dir = manifest_file.parent
            artifact_path = str(artifact_dir.relative_to(storage_path))
            total_artifacts += 1

            if ctx.debug:
                click.echo(f"Processing artifact: {artifact_path}", err=True)

            # Read manifest to get tagged hashes
            # Manifest stores full hashes, but blobs are stored with short hashes (8 chars)
            manifest = read_manifest(artifact_dir)
            tagged_hashes = {h[:8] for h in manifest.tags.values()}

            # Reconcile symlinks for this artifact
            stats = reconcile_symlinks(artifact_dir, manifest)
            symlinks_checked += stats.checked
            symlinks_fixed += stats.fixed

            # Build detail string if there were fixes for this artifact
            if stats.fixed > 0:
                details_parts = []
                if stats.created_tags:
                    details_parts.append(f"created '{', '.join(stats.created_tags)}'")
                if stats.removed_tags:
                    details_parts.append(f"removed '{', '.join(stats.removed_tags)}'")
                if stats.updated_tags:
                    details_parts.append(f"updated '{', '.join(stats.updated_tags)}'")
                symlinks_fixed_details.append(f"{artifact_path}: {', '.join(details_parts)}")

            if not reconcile_only:
                # Find all blobs in blobs/ directory
                blobs_dir = artifact_dir / "blobs"
                if blobs_dir.exists():
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
                            if ctx.debug:
                                click.echo(
                                    f"  Skipping blob {blob_hash[:12]}... (unknown age)", err=True
                                )
                            continue

                        # Check if blob is older than retention period
                        if blob_age_days < retention_days:
                            if ctx.debug:
                                click.echo(
                                    f"  Keeping blob {blob_hash[:12]}... "
                                    f"({blob_age_days} days old < {retention_days})",
                                    err=True,
                                )
                            continue

                        # Mark blob for deletion
                        blob_size = blob_file.stat().st_size
                        metadata_file = artifact_dir / "metadata" / f"{blob_hash}.json"

                        blobs_to_delete.append(
                            BlobToDelete(
                                blob_file=blob_file,
                                artifact_path=artifact_path,
                                blob_hash=blob_hash,
                                age_days=blob_age_days,
                                size=blob_size,
                                metadata_file=metadata_file if metadata_file.exists() else None,
                            )
                        )

            # Update progress
            if progress is not None and task_id is not None:
                progress.update(task_id, advance=1)

    # Phase 2: Delete blobs (if not reconcile-only)
    deleted_blobs = 0
    if not reconcile_only and blobs_to_delete:
        if dry_run:
            # In dry-run mode, just print what would be deleted
            for blob in blobs_to_delete:
                click.echo(
                    f"Would delete: {blob.artifact_path}/blobs/{blob.blob_hash[:12]}... "
                    f"({blob.age_days} days old, {_format_size(blob.size)})"
                )
                deleted_bytes += blob.size
            deleted_blobs = len(blobs_to_delete)
        else:
            # Actually delete blobs with progress bar
            with count_progress("Deleting blobs", len(blobs_to_delete), quiet=quiet) as (
                progress,
                task_id,
            ):
                for blob in blobs_to_delete:
                    if ctx.debug:
                        click.echo(
                            f"  Deleting blob {blob.blob_hash[:12]}... ({blob.age_days} days old)",
                            err=True,
                        )

                    blob.blob_file.unlink()

                    # Also delete metadata sidecar if it exists
                    if blob.metadata_file is not None:
                        blob.metadata_file.unlink()

                    deleted_blobs += 1
                    deleted_bytes += blob.size

                    # Update progress
                    if progress is not None and task_id is not None:
                        progress.update(task_id, advance=1)

    # Print summary
    click.echo("")
    click.echo("GC Summary:")
    click.echo(f"  Artifacts scanned: {total_artifacts}")
    click.echo(f"  Blobs found: {total_blobs_found}")
    click.echo(f"  Untagged blobs: {untagged_blobs}")
    click.echo(f"  Symlinks checked: {symlinks_checked}")

    # Display symlinks fixed with optional details
    if symlinks_fixed == 0:
        click.echo(f"  Symlinks fixed: {symlinks_fixed}")
    else:
        details_str = "; ".join(symlinks_fixed_details)
        click.echo(f"  Symlinks fixed: {symlinks_fixed} ({details_str})")

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
