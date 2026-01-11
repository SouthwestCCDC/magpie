"""GC command for garbage collecting untagged blobs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import click

from magpie.ctl import CTLContext
from magpie.storage.manifest import Manifest, read_manifest
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
@click.pass_obj
def gc(ctx: CTLContext, dry_run: bool, reconcile_only: bool) -> None:
    """Garbage collect untagged blobs older than retention period.

    Walks all artifact directories and:
    - Identifies blobs not referenced by any tag
    - Deletes untagged blobs older than retention_days (default 90)
    - Reconciles symlinks to match manifests

    Use --dry-run to see what would be deleted without making changes.
    Use --reconcile-only to only fix symlinks without deleting blobs.

    Examples:

        magpie-ctl gc --dry-run

        magpie-ctl gc

        magpie-ctl gc --reconcile-only
    """
    settings = ctx.settings
    storage_path = settings.storage_path
    retention_days = settings.retention_days

    if not storage_path.exists():
        raise click.ClickException(f"Storage path does not exist: {storage_path}")

    if ctx.debug:
        click.echo(f"Storage path: {storage_path}", err=True)
        click.echo(f"Retention days: {retention_days}", err=True)

    # Track statistics
    total_artifacts = 0
    total_blobs_found = 0
    untagged_blobs = 0
    deleted_blobs = 0
    deleted_bytes = 0
    reconciled_artifacts = 0

    now = datetime.now(timezone.utc)

    # Find all artifact directories (containing .magpie manifest)
    for manifest_file in storage_path.rglob(".magpie"):
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
        reconcile_symlinks(artifact_dir, manifest)
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
                if ctx.debug:
                    click.echo(f"  Skipping blob {blob_hash[:12]}... (unknown age)", err=True)
                continue

            # Check if blob is older than retention period
            if blob_age_days < retention_days:
                if ctx.debug:
                    click.echo(
                        f"  Keeping blob {blob_hash[:12]}... ({blob_age_days} days old < {retention_days})",
                        err=True,
                    )
                continue

            # Delete old untagged blob
            blob_size = blob_file.stat().st_size

            if dry_run:
                click.echo(
                    f"Would delete: {artifact_path}/blobs/{blob_hash[:12]}... "
                    f"({blob_age_days} days old, {_format_size(blob_size)})"
                )
            else:
                if ctx.debug:
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

    # Print summary
    click.echo("")
    click.echo("GC Summary:")
    click.echo(f"  Artifacts scanned: {total_artifacts}")
    click.echo(f"  Blobs found: {total_blobs_found}")
    click.echo(f"  Untagged blobs: {untagged_blobs}")
    click.echo(f"  Symlinks reconciled: {reconciled_artifacts} artifact(s)")

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
