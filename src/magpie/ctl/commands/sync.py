"""Sync command for S3 backup and restore operations."""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - subprocess needed for rclone/aws CLI calls
from pathlib import Path
from typing import TYPE_CHECKING

import click

from magpie.cli.formatting import (
    CommandResult,
    ErrorCode,
    is_json_output,
    output_error,
    output_result,
)
from magpie.cli.progress import count_progress
from magpie.ctl import CTLContext
from magpie.storage.manifest import Manifest, read_manifest
from magpie.utils.formatting import format_size

if TYPE_CHECKING:
    from collections.abc import Iterator

# rclone or aws CLI - prefer rclone if available
RCLONE_CMD = "rclone"
AWS_CMD = "aws"


def _find_sync_tool() -> str | None:
    """Find available sync tool (rclone preferred, fallback to aws cli).

    Returns:
        Name of the sync tool to use, or None if neither is available.
    """
    if shutil.which(RCLONE_CMD):
        return RCLONE_CMD
    if shutil.which(AWS_CMD):
        return AWS_CMD
    return None


def _iter_tagged_artifacts(storage_path: Path) -> Iterator[tuple[Path, Manifest]]:
    """Iterate over all artifacts that have at least one tag.

    Yields:
        Tuple of (artifact_dir, manifest) for each tagged artifact.
    """
    for manifest_file in storage_path.rglob(".magpie"):
        manifest = read_manifest(manifest_file.parent)
        if manifest.tags:
            yield manifest_file.parent, manifest


def _get_blobs_for_manifest(artifact_dir: Path, manifest: Manifest) -> list[Path]:
    """Get all blob files referenced by tags in a manifest.

    Args:
        artifact_dir: Path to the artifact directory.
        manifest: The manifest containing tag -> hash mappings.

    Returns:
        List of blob file paths that are referenced by tags.
    """
    blobs = []
    blobs_dir = artifact_dir / "blobs"
    if not blobs_dir.exists():
        return blobs

    # Get unique hashes from tags
    unique_hashes = set(manifest.tags.values())

    for hash_ref in unique_hashes:
        # Hash refs are stored as "@abc12345" - strip @ and use first 8 chars
        blob_name = hash_ref.lstrip("@")[:8]
        blob_path = blobs_dir / blob_name
        if blob_path.exists():
            blobs.append(blob_path)

    return blobs


def _get_metadata_for_manifest(artifact_dir: Path, manifest: Manifest) -> list[Path]:
    """Get all metadata files referenced by tags in a manifest.

    Args:
        artifact_dir: Path to the artifact directory.
        manifest: The manifest containing tag -> hash mappings.

    Returns:
        List of metadata file paths that are referenced by tags.
    """
    metadata_files = []
    metadata_dir = artifact_dir / "metadata"
    if not metadata_dir.exists():
        return metadata_files

    # Get unique hashes from tags
    unique_hashes = set(manifest.tags.values())

    for hash_ref in unique_hashes:
        # Hash refs are stored as "@abc12345" - strip @ and use first 8 chars
        metadata_name = f"{hash_ref.lstrip('@')[:8]}.json"
        metadata_path = metadata_dir / metadata_name
        if metadata_path.exists():
            metadata_files.append(metadata_path)

    return metadata_files


def _build_s3_path(bucket: str, prefix: str, relative_path: str) -> str:
    """Build S3 path from bucket, prefix, and relative path.

    Args:
        bucket: S3 bucket name.
        prefix: Optional prefix (may be empty).
        relative_path: Path relative to storage root.

    Returns:
        Full S3 path like s3://bucket/prefix/path or s3://bucket/path.
    """
    parts = [f"s3://{bucket}"]
    if prefix:
        parts.append(prefix.strip("/"))
    if relative_path:
        parts.append(relative_path.strip("/"))
    return "/".join(parts)


def _sync_to_s3_rclone(
    storage_path: Path,
    bucket: str,
    prefix: str,
    files_to_sync: list[tuple[Path, str]],
    dry_run: bool,
    debug: bool,
    quiet: bool,
) -> tuple[int, int, list[str]]:
    """Sync files to S3 using rclone.

    Uses rclone copy with checksum-based comparison for efficiency.

    Args:
        storage_path: Local storage path.
        bucket: S3 bucket name.
        prefix: S3 key prefix.
        files_to_sync: List of (local_path, relative_path) tuples.
        dry_run: If True, show what would be synced without actually syncing.
        debug: If True, show debug output.
        quiet: If True, suppress progress output.

    Returns:
        Tuple of (files_synced, bytes_synced, errors).
    """
    if not files_to_sync:
        return 0, 0, []

    errors = []
    files_synced = 0
    bytes_synced = 0

    # Build rclone destination
    dest_base = f"s3://{bucket}"
    if prefix:
        dest_base = f"{dest_base}/{prefix.strip('/')}"

    # Sync each file using rclone copyto (for individual files)
    with count_progress("Syncing to S3", len(files_to_sync), quiet=quiet) as (progress, task_id):
        for local_path, relative_path in files_to_sync:
            dest_path = f"{dest_base}/{relative_path}"

            cmd = [
                RCLONE_CMD,
                "copyto",
                str(local_path),
                dest_path,
                "--checksum",
                "-v" if debug else "-q",
            ]

            if dry_run:
                cmd.append("--dry-run")

            if debug:
                click.echo(f"Running: {' '.join(cmd)}", err=True)

            try:
                result = subprocess.run(  # nosec B603 - cmd args are validated config values
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    errors.append(f"Failed to sync {relative_path}: {result.stderr}")
                else:
                    files_synced += 1
                    bytes_synced += local_path.stat().st_size
            except Exception as e:
                errors.append(f"Error syncing {relative_path}: {e}")

            if progress and task_id is not None:
                progress.update(task_id, advance=1)

    return files_synced, bytes_synced, errors


def _sync_to_s3_aws(
    storage_path: Path,
    bucket: str,
    prefix: str,
    files_to_sync: list[tuple[Path, str]],
    dry_run: bool,
    debug: bool,
    quiet: bool,
) -> tuple[int, int, list[str]]:
    """Sync files to S3 using AWS CLI.

    Uses aws s3 cp with size/timestamp comparison.

    Args:
        storage_path: Local storage path.
        bucket: S3 bucket name.
        prefix: S3 key prefix.
        files_to_sync: List of (local_path, relative_path) tuples.
        dry_run: If True, show what would be synced without actually syncing.
        debug: If True, show debug output.
        quiet: If True, suppress progress output.

    Returns:
        Tuple of (files_synced, bytes_synced, errors).
    """
    if not files_to_sync:
        return 0, 0, []

    errors = []
    files_synced = 0
    bytes_synced = 0

    # Build S3 destination base
    dest_base = f"s3://{bucket}"
    if prefix:
        dest_base = f"{dest_base}/{prefix.strip('/')}"

    # Copy each file
    with count_progress("Syncing to S3", len(files_to_sync), quiet=quiet) as (progress, task_id):
        for local_path, relative_path in files_to_sync:
            dest_path = f"{dest_base}/{relative_path}"

            cmd = [
                AWS_CMD,
                "s3",
                "cp",
                str(local_path),
                dest_path,
            ]

            if dry_run:
                cmd.append("--dryrun")

            if not debug:
                cmd.append("--quiet")

            if debug:
                click.echo(f"Running: {' '.join(cmd)}", err=True)

            try:
                result = subprocess.run(  # nosec B603 - cmd args are validated config values
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    errors.append(f"Failed to sync {relative_path}: {result.stderr}")
                else:
                    files_synced += 1
                    bytes_synced += local_path.stat().st_size
            except Exception as e:
                errors.append(f"Error syncing {relative_path}: {e}")

            if progress and task_id is not None:
                progress.update(task_id, advance=1)

    return files_synced, bytes_synced, errors


def _sync_from_s3_rclone(
    storage_path: Path,
    bucket: str,
    prefix: str,
    dry_run: bool,
    debug: bool,
    quiet: bool,
) -> tuple[int, int, list[str]]:
    """Sync from S3 to local storage using rclone.

    Args:
        storage_path: Local storage path.
        bucket: S3 bucket name.
        prefix: S3 key prefix.
        dry_run: If True, show what would be synced without actually syncing.
        debug: If True, show debug output.
        quiet: If True, suppress progress output.

    Returns:
        Tuple of (files_synced, bytes_synced, errors).
    """
    errors = []

    # Build source path
    src_base = f"s3://{bucket}"
    if prefix:
        src_base = f"{src_base}/{prefix.strip('/')}"

    cmd = [
        RCLONE_CMD,
        "sync",
        src_base,
        str(storage_path),
        "--checksum",
        "-v" if debug else "-q",
    ]

    if dry_run:
        cmd.append("--dry-run")

    if debug:
        click.echo(f"Running: {' '.join(cmd)}", err=True)

    if not quiet and not is_json_output():
        click.echo("Syncing from S3 (this may take a while)...")

    try:
        result = subprocess.run(  # nosec B603 - cmd args are validated config values
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            errors.append(f"rclone sync failed: {result.stderr}")
            return 0, 0, errors

        # Parse rclone output for stats (approximate)
        # rclone outputs stats in verbose mode
        if debug:
            click.echo(result.stdout, err=True)
            click.echo(result.stderr, err=True)

    except Exception as e:
        errors.append(f"Error running rclone: {e}")
        return 0, 0, errors

    # Count files restored by walking storage
    files_restored = 0
    bytes_restored = 0
    for manifest_file in storage_path.rglob(".magpie"):
        files_restored += 1
        artifact_dir = manifest_file.parent
        blobs_dir = artifact_dir / "blobs"
        if blobs_dir.exists():
            for blob in blobs_dir.iterdir():
                if blob.is_file():
                    files_restored += 1
                    bytes_restored += blob.stat().st_size

    return files_restored, bytes_restored, errors


def _sync_from_s3_aws(
    storage_path: Path,
    bucket: str,
    prefix: str,
    dry_run: bool,
    debug: bool,
    quiet: bool,
) -> tuple[int, int, list[str]]:
    """Sync from S3 to local storage using AWS CLI.

    Args:
        storage_path: Local storage path.
        bucket: S3 bucket name.
        prefix: S3 key prefix.
        dry_run: If True, show what would be synced without actually syncing.
        debug: If True, show debug output.
        quiet: If True, suppress progress output.

    Returns:
        Tuple of (files_synced, bytes_synced, errors).
    """
    errors = []

    # Build source path
    src_base = f"s3://{bucket}"
    if prefix:
        src_base = f"{src_base}/{prefix.strip('/')}"

    cmd = [
        AWS_CMD,
        "s3",
        "sync",
        src_base,
        str(storage_path),
    ]

    if dry_run:
        cmd.append("--dryrun")

    if not debug:
        cmd.append("--quiet")

    if debug:
        click.echo(f"Running: {' '.join(cmd)}", err=True)

    if not quiet and not is_json_output():
        click.echo("Syncing from S3 (this may take a while)...")

    try:
        result = subprocess.run(  # nosec B603 - cmd args are validated config values
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            errors.append(f"aws s3 sync failed: {result.stderr}")
            return 0, 0, errors

        if debug:
            click.echo(result.stdout, err=True)
            click.echo(result.stderr, err=True)

    except Exception as e:
        errors.append(f"Error running aws cli: {e}")
        return 0, 0, errors

    # Count files restored by walking storage
    files_restored = 0
    bytes_restored = 0
    for manifest_file in storage_path.rglob(".magpie"):
        files_restored += 1
        artifact_dir = manifest_file.parent
        blobs_dir = artifact_dir / "blobs"
        if blobs_dir.exists():
            for blob in blobs_dir.iterdir():
                if blob.is_file():
                    files_restored += 1
                    bytes_restored += blob.stat().st_size

    return files_restored, bytes_restored, errors


def _has_existing_data(storage_path: Path) -> bool:
    """Check if storage path contains existing artifact data.

    Args:
        storage_path: Path to check.

    Returns:
        True if any .magpie manifest files exist.
    """
    if not storage_path.exists():
        return False
    return any(storage_path.rglob(".magpie"))


def _verify_restored_data(storage_path: Path) -> tuple[int, int, list[str]]:
    """Verify integrity of restored data.

    Checks that all manifests are valid JSON and all referenced blobs exist.

    Args:
        storage_path: Path to storage directory.

    Returns:
        Tuple of (artifacts_verified, blobs_verified, errors).
    """
    artifacts_verified = 0
    blobs_verified = 0
    errors = []

    for manifest_file in storage_path.rglob(".magpie"):
        artifact_dir = manifest_file.parent
        relative_path = artifact_dir.relative_to(storage_path)

        try:
            manifest = read_manifest(artifact_dir)
            artifacts_verified += 1

            # Check all referenced blobs exist
            for tag_name, hash_ref in manifest.tags.items():
                blob_name = hash_ref.lstrip("@")[:8]
                blob_path = artifact_dir / "blobs" / blob_name
                if not blob_path.exists():
                    errors.append(f"Missing blob for {relative_path}:{tag_name} - {blob_name}")
                else:
                    blobs_verified += 1

        except Exception as e:
            errors.append(f"Invalid manifest at {relative_path}: {e}")

    return artifacts_verified, blobs_verified, errors


@click.group()
def sync() -> None:
    """Sync artifacts to/from S3 for disaster recovery.

    Requires MAGPIE_S3_BUCKET environment variable to be set.
    Optionally set MAGPIE_S3_PREFIX to add a prefix to all S3 keys.

    AWS credentials should be configured via environment variables
    (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY) or IAM role.
    """
    pass


@sync.command("to-s3")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be synced without actually syncing.",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress progress output.",
)
@click.pass_obj
def to_s3(ctx: CTLContext, dry_run: bool, quiet: bool) -> None:
    """Sync tagged artifacts and their blobs to S3.

    Only artifacts with at least one tag are synced. Untagged blobs
    are not backed up (they should be cleaned up by GC anyway).

    Uses rclone if available (preferred), otherwise falls back to AWS CLI.
    Sync is incremental - only changed files are uploaded.

    Examples:

        magpie-ctl sync to-s3

        magpie-ctl sync to-s3 --dry-run

        MAGPIE_S3_BUCKET=my-backup-bucket magpie-ctl sync to-s3
    """
    settings = ctx.settings
    storage_path = settings.storage_path

    # Check S3 configuration
    if not settings.s3_bucket:
        msg = "MAGPIE_S3_BUCKET environment variable is required"
        if is_json_output():
            output_error(ErrorCode.CONFIG_ERROR, msg)
        else:
            raise click.ClickException(msg)

    # Check storage path exists
    if not storage_path.exists():
        msg = f"Storage path does not exist: {storage_path}"
        if is_json_output():
            output_error(ErrorCode.IO_ERROR, msg)
        else:
            raise click.ClickException(msg)

    # Find sync tool
    sync_tool = _find_sync_tool()
    if not sync_tool:
        msg = "Neither rclone nor aws CLI found. Please install one of them."
        if is_json_output():
            output_error(ErrorCode.CONFIG_ERROR, msg)
        else:
            raise click.ClickException(msg)

    if ctx.debug:
        click.echo(f"Using sync tool: {sync_tool}", err=True)
        click.echo(f"Storage path: {storage_path}", err=True)
        click.echo(f"S3 bucket: {settings.s3_bucket}", err=True)
        click.echo(f"S3 prefix: {settings.s3_prefix or '(none)'}", err=True)

    # Collect files to sync
    files_to_sync: list[tuple[Path, str]] = []
    artifacts_found = 0
    blobs_found = 0
    total_bytes = 0

    if not quiet and not is_json_output():
        click.echo("Scanning for tagged artifacts...")

    for artifact_dir, manifest in _iter_tagged_artifacts(storage_path):
        artifacts_found += 1
        relative_artifact_path = str(artifact_dir.relative_to(storage_path))

        # Add manifest file
        manifest_path = artifact_dir / ".magpie"
        if manifest_path.exists():
            files_to_sync.append((manifest_path, f"{relative_artifact_path}/.magpie"))
            total_bytes += manifest_path.stat().st_size

        # Add blobs
        for blob_path in _get_blobs_for_manifest(artifact_dir, manifest):
            blobs_found += 1
            relative_blob_path = f"{relative_artifact_path}/blobs/{blob_path.name}"
            files_to_sync.append((blob_path, relative_blob_path))
            total_bytes += blob_path.stat().st_size

        # Add metadata
        for metadata_path in _get_metadata_for_manifest(artifact_dir, manifest):
            relative_metadata_path = f"{relative_artifact_path}/metadata/{metadata_path.name}"
            files_to_sync.append((metadata_path, relative_metadata_path))
            total_bytes += metadata_path.stat().st_size

    if ctx.debug:
        click.echo(f"Found {artifacts_found} tagged artifacts", err=True)
        click.echo(f"Found {blobs_found} blobs to sync", err=True)
        click.echo(f"Total size: {format_size(total_bytes)}", err=True)

    if not files_to_sync:
        if is_json_output():
            output_result(
                CommandResult(
                    data={
                        "dry_run": dry_run,
                        "artifacts_found": 0,
                        "blobs_found": 0,
                        "files_synced": 0,
                        "bytes_synced": 0,
                        "errors": [],
                    },
                    human_output="",
                )
            )
        else:
            click.echo("No tagged artifacts found to sync.")
        return

    # Perform sync
    if sync_tool == RCLONE_CMD:
        files_synced, bytes_synced, errors = _sync_to_s3_rclone(
            storage_path,
            settings.s3_bucket,
            settings.s3_prefix,
            files_to_sync,
            dry_run,
            ctx.debug,
            quiet or is_json_output(),
        )
    else:
        files_synced, bytes_synced, errors = _sync_to_s3_aws(
            storage_path,
            settings.s3_bucket,
            settings.s3_prefix,
            files_to_sync,
            dry_run,
            ctx.debug,
            quiet or is_json_output(),
        )

    # Output results
    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "dry_run": dry_run,
                    "sync_tool": sync_tool,
                    "artifacts_found": artifacts_found,
                    "blobs_found": blobs_found,
                    "files_synced": files_synced,
                    "bytes_synced": bytes_synced,
                    "errors": errors,
                },
                human_output="",
            )
        )
    else:
        action = "Would sync" if dry_run else "Synced"
        click.echo("")
        click.echo(f"S3 Backup {'Preview (dry run)' if dry_run else 'Complete'}:")
        click.echo(f"  Sync tool: {sync_tool}")
        click.echo(f"  Artifacts found: {artifacts_found}")
        click.echo(f"  Blobs found: {blobs_found}")
        click.echo(f"  {action}: {files_synced} file(s), {format_size(bytes_synced)}")

        if errors:
            click.echo("")
            click.echo("Errors:")
            for error in errors:
                click.echo(f"  - {error}", err=True)
            raise SystemExit(1)


@sync.command("from-s3")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be restored without actually restoring.",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress progress output.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Allow restore even if data already exists (WARNING: may overwrite).",
)
@click.option(
    "--skip-verify",
    is_flag=True,
    help="Skip integrity verification after restore.",
)
@click.pass_obj
def from_s3(
    ctx: CTLContext,
    dry_run: bool,
    quiet: bool,
    force: bool,
    skip_verify: bool,
) -> None:
    """Restore artifacts from S3 to local storage.

    By default, refuses to restore if data already exists to prevent
    accidental overwrites. Use --force to override this check.

    Uses rclone if available (preferred), otherwise falls back to AWS CLI.

    Examples:

        magpie-ctl sync from-s3

        magpie-ctl sync from-s3 --dry-run

        magpie-ctl sync from-s3 --force

        MAGPIE_S3_BUCKET=my-backup-bucket magpie-ctl sync from-s3
    """
    settings = ctx.settings
    storage_path = settings.storage_path

    # Check S3 configuration
    if not settings.s3_bucket:
        msg = "MAGPIE_S3_BUCKET environment variable is required"
        if is_json_output():
            output_error(ErrorCode.CONFIG_ERROR, msg)
        else:
            raise click.ClickException(msg)

    # Check for existing data
    if not dry_run and _has_existing_data(storage_path) and not force:
        msg = (
            "Storage path already contains data. "
            "Use --force to restore anyway (may overwrite existing data)."
        )
        if is_json_output():
            output_error(ErrorCode.CONFLICT, msg)
        else:
            raise click.ClickException(msg)

    # Find sync tool
    sync_tool = _find_sync_tool()
    if not sync_tool:
        msg = "Neither rclone nor aws CLI found. Please install one of them."
        if is_json_output():
            output_error(ErrorCode.CONFIG_ERROR, msg)
        else:
            raise click.ClickException(msg)

    if ctx.debug:
        click.echo(f"Using sync tool: {sync_tool}", err=True)
        click.echo(f"Storage path: {storage_path}", err=True)
        click.echo(f"S3 bucket: {settings.s3_bucket}", err=True)
        click.echo(f"S3 prefix: {settings.s3_prefix or '(none)'}", err=True)

    # Ensure storage path exists
    if not dry_run:
        storage_path.mkdir(parents=True, exist_ok=True)

    # Perform restore
    if sync_tool == RCLONE_CMD:
        files_restored, bytes_restored, errors = _sync_from_s3_rclone(
            storage_path,
            settings.s3_bucket,
            settings.s3_prefix,
            dry_run,
            ctx.debug,
            quiet or is_json_output(),
        )
    else:
        files_restored, bytes_restored, errors = _sync_from_s3_aws(
            storage_path,
            settings.s3_bucket,
            settings.s3_prefix,
            dry_run,
            ctx.debug,
            quiet or is_json_output(),
        )

    # Verify integrity if not dry-run and not skipped
    verification_errors: list[str] = []
    artifacts_verified = 0
    blobs_verified = 0

    if not dry_run and not skip_verify and not errors:
        if not quiet and not is_json_output():
            click.echo("Verifying restored data...")

        artifacts_verified, blobs_verified, verification_errors = _verify_restored_data(
            storage_path
        )

        if ctx.debug:
            click.echo(f"Verified {artifacts_verified} artifacts", err=True)
            click.echo(f"Verified {blobs_verified} blobs", err=True)

    # Combine all errors
    all_errors = errors + verification_errors

    # Output results
    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "dry_run": dry_run,
                    "sync_tool": sync_tool,
                    "files_restored": files_restored,
                    "bytes_restored": bytes_restored,
                    "artifacts_verified": artifacts_verified,
                    "blobs_verified": blobs_verified,
                    "errors": all_errors,
                },
                human_output="",
            )
        )
    else:
        action = "Would restore" if dry_run else "Restored"
        click.echo("")
        click.echo(f"S3 Restore {'Preview (dry run)' if dry_run else 'Complete'}:")
        click.echo(f"  Sync tool: {sync_tool}")
        click.echo(f"  {action}: {files_restored} file(s), {format_size(bytes_restored)}")

        if not dry_run and not skip_verify and not errors:
            click.echo(f"  Artifacts verified: {artifacts_verified}")
            click.echo(f"  Blobs verified: {blobs_verified}")

        if all_errors:
            click.echo("")
            click.echo("Errors:")
            for error in all_errors:
                click.echo(f"  - {error}", err=True)
            raise SystemExit(1)


def _list_s3_files_rclone(bucket: str, prefix: str, pattern: str, debug: bool) -> list[str]:
    """List files in S3 matching a pattern using rclone.

    Args:
        bucket: S3 bucket name.
        prefix: S3 key prefix.
        pattern: Glob pattern to filter files (e.g., "*.magpie" or "*/blobs/*").
        debug: If True, show debug output.

    Returns:
        List of relative paths matching the pattern.
    """
    # Build source path
    src_base = f"s3://{bucket}"
    if prefix:
        src_base = f"{src_base}/{prefix.strip('/')}"

    cmd = [
        RCLONE_CMD,
        "lsf",
        src_base,
        "--recursive",
        "--files-only",
        "--include",
        pattern,
    ]

    if debug:
        click.echo(f"Running: {' '.join(cmd)}", err=True)

    try:
        result = subprocess.run(  # nosec B603 - cmd args are validated config values
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            if debug:
                click.echo(f"rclone lsf failed: {result.stderr}", err=True)
            return []

        # Parse output - one file per line
        return [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
    except Exception as e:
        if debug:
            click.echo(f"Error running rclone: {e}", err=True)
        return []


def _list_s3_files_aws(bucket: str, prefix: str, pattern: str, debug: bool) -> list[str]:
    """List files in S3 matching a pattern using AWS CLI.

    Args:
        bucket: S3 bucket name.
        prefix: S3 key prefix.
        pattern: Glob pattern to filter files (e.g., "*.magpie" or "*/blobs/*").
        debug: If True, show debug output.

    Returns:
        List of relative paths matching the pattern.
    """
    import fnmatch

    # Build S3 prefix for listing
    s3_prefix = prefix.strip("/") + "/" if prefix else ""

    cmd = [
        AWS_CMD,
        "s3",
        "ls",
        f"s3://{bucket}/{s3_prefix}",
        "--recursive",
    ]

    if debug:
        click.echo(f"Running: {' '.join(cmd)}", err=True)

    try:
        result = subprocess.run(  # nosec B603 - cmd args are validated config values
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            if debug:
                click.echo(f"aws s3 ls failed: {result.stderr}", err=True)
            return []

        # Parse output - format is: date time size key
        # Example: 2024-01-01 12:00:00  1234 prefix/path/file.txt
        files = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            # Split on whitespace, key is the last part
            parts = line.split()
            if len(parts) >= 4:
                # Key is everything after date, time, size
                key = " ".join(parts[3:])
                # Remove the prefix to get relative path
                if s3_prefix and key.startswith(s3_prefix):
                    relative_path = key[len(s3_prefix) :]
                else:
                    relative_path = key

                # Filter by pattern using fnmatch
                if fnmatch.fnmatch(relative_path, pattern):
                    files.append(relative_path)

        return files
    except Exception as e:
        if debug:
            click.echo(f"Error running aws cli: {e}", err=True)
        return []


def _get_s3_file_content_rclone(bucket: str, prefix: str, path: str, debug: bool) -> str | None:
    """Get file content from S3 using rclone.

    Args:
        bucket: S3 bucket name.
        prefix: S3 key prefix.
        path: Relative path within the prefix.
        debug: If True, show debug output.

    Returns:
        File content as string, or None on error.
    """
    # Build full S3 path
    s3_path = _build_s3_path(bucket, prefix, path)

    cmd = [
        RCLONE_CMD,
        "cat",
        s3_path,
    ]

    if debug:
        click.echo(f"Running: {' '.join(cmd)}", err=True)

    try:
        result = subprocess.run(  # nosec B603 - cmd args are validated config values
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            if debug:
                click.echo(f"rclone cat failed: {result.stderr}", err=True)
            return None
        return result.stdout
    except Exception as e:
        if debug:
            click.echo(f"Error running rclone: {e}", err=True)
        return None


def _get_s3_file_content_aws(bucket: str, prefix: str, path: str, debug: bool) -> str | None:
    """Get file content from S3 using AWS CLI.

    Args:
        bucket: S3 bucket name.
        prefix: S3 key prefix.
        path: Relative path within the prefix.
        debug: If True, show debug output.

    Returns:
        File content as string, or None on error.
    """
    import tempfile

    # Build full S3 key
    s3_key = f"{prefix.strip('/')}/{path}" if prefix else path

    # Create temp file to download to
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name

    try:
        cmd = [
            AWS_CMD,
            "s3",
            "cp",
            f"s3://{bucket}/{s3_key}",
            tmp_path,
            "--quiet",
        ]

        if debug:
            click.echo(f"Running: {' '.join(cmd)}", err=True)

        result = subprocess.run(  # nosec B603 - cmd args are validated config values
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            if debug:
                click.echo(f"aws s3 cp failed: {result.stderr}", err=True)
            return None

        # Read content from temp file
        with open(tmp_path, encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        if debug:
            click.echo(f"Error running aws cli: {e}", err=True)
        return None
    finally:
        # Clean up temp file
        Path(tmp_path).unlink(missing_ok=True)


def _delete_s3_file_rclone(bucket: str, prefix: str, path: str, debug: bool) -> bool:
    """Delete a file from S3 using rclone.

    Args:
        bucket: S3 bucket name.
        prefix: S3 key prefix.
        path: Relative path within the prefix.
        debug: If True, show debug output.

    Returns:
        True if deleted successfully, False otherwise.
    """
    s3_path = _build_s3_path(bucket, prefix, path)

    cmd = [
        RCLONE_CMD,
        "deletefile",
        s3_path,
    ]

    if debug:
        click.echo(f"Running: {' '.join(cmd)}", err=True)

    try:
        result = subprocess.run(  # nosec B603 - cmd args are validated config values
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except Exception as e:
        if debug:
            click.echo(f"Error running rclone: {e}", err=True)
        return False


def _delete_s3_file_aws(bucket: str, prefix: str, path: str, debug: bool) -> bool:
    """Delete a file from S3 using AWS CLI.

    Args:
        bucket: S3 bucket name.
        prefix: S3 key prefix.
        path: Relative path within the prefix.
        debug: If True, show debug output.

    Returns:
        True if deleted successfully, False otherwise.
    """
    s3_key = f"{prefix.strip('/')}/{path}" if prefix else path

    cmd = [
        AWS_CMD,
        "s3",
        "rm",
        f"s3://{bucket}/{s3_key}",
    ]

    if debug:
        click.echo(f"Running: {' '.join(cmd)}", err=True)

    try:
        result = subprocess.run(  # nosec B603 - cmd args are validated config values
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except Exception as e:
        if debug:
            click.echo(f"Error running aws cli: {e}", err=True)
        return False


def _get_s3_file_size_rclone(
    bucket: str, prefix: str, paths: list[str], debug: bool
) -> dict[str, int]:
    """Get file sizes from S3 using rclone.

    Args:
        bucket: S3 bucket name.
        prefix: S3 key prefix.
        paths: List of relative paths to get sizes for.
        debug: If True, show debug output.

    Returns:
        Dict mapping path to size in bytes.
    """
    if not paths:
        return {}

    # Build source path
    src_base = f"s3://{bucket}"
    if prefix:
        src_base = f"{src_base}/{prefix.strip('/')}"

    # Use rclone lsl to get sizes (long listing)
    cmd = [
        RCLONE_CMD,
        "lsl",
        src_base,
        "--recursive",
        "--files-only",
    ]

    if debug:
        click.echo(f"Running: {' '.join(cmd)}", err=True)

    try:
        result = subprocess.run(  # nosec B603 - cmd args are validated config values
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return {}

        # Parse output - format: size date time path
        sizes = {}
        paths_set = set(paths)
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 4:
                try:
                    size = int(parts[0])
                    # Path is the rest after size, date, time
                    path = " ".join(parts[3:])
                    if path in paths_set:
                        sizes[path] = size
                except (ValueError, IndexError):
                    continue
        return sizes
    except Exception as e:
        if debug:
            click.echo(f"Error running rclone: {e}", err=True)
        return {}


def _get_s3_file_size_aws(
    bucket: str, prefix: str, paths: list[str], debug: bool
) -> dict[str, int]:
    """Get file sizes from S3 using AWS CLI.

    Args:
        bucket: S3 bucket name.
        prefix: S3 key prefix.
        paths: List of relative paths to get sizes for.
        debug: If True, show debug output.

    Returns:
        Dict mapping path to size in bytes.
    """
    if not paths:
        return {}

    # Build S3 prefix for listing
    s3_prefix = prefix.strip("/") + "/" if prefix else ""

    cmd = [
        AWS_CMD,
        "s3",
        "ls",
        f"s3://{bucket}/{s3_prefix}",
        "--recursive",
    ]

    if debug:
        click.echo(f"Running: {' '.join(cmd)}", err=True)

    try:
        result = subprocess.run(  # nosec B603 - cmd args are validated config values
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return {}

        # Parse output - format: date time size key
        sizes = {}
        paths_set = set(paths)
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 4:
                try:
                    size = int(parts[2])
                    key = " ".join(parts[3:])
                    if s3_prefix and key.startswith(s3_prefix):
                        relative_path = key[len(s3_prefix) :]
                    else:
                        relative_path = key
                    if relative_path in paths_set:
                        sizes[relative_path] = size
                except (ValueError, IndexError):
                    continue
        return sizes
    except Exception as e:
        if debug:
            click.echo(f"Error running aws cli: {e}", err=True)
        return {}


def _parse_manifest_content(content: str) -> set[str]:
    """Parse manifest JSON and extract tagged blob hashes.

    Args:
        content: JSON content of the manifest file.

    Returns:
        Set of blob hash names (8-char prefixes, without @ symbol).
    """
    import json

    try:
        data = json.loads(content)
        tags = data.get("tags", {})
        # Extract blob names from hash refs (e.g., "@abc12345" -> "abc12345")
        return {hash_ref.lstrip("@")[:8] for hash_ref in tags.values() if hash_ref}
    except (json.JSONDecodeError, KeyError, TypeError):
        return set()


def _extract_blob_name_from_path(path: str) -> str | None:
    """Extract blob name from a blob path.

    Args:
        path: Path like "artifact/blobs/abc12345" or "proj/artifact/blobs/def67890".

    Returns:
        Blob name (e.g., "abc12345") or None if path doesn't look like a blob.
    """
    parts = path.split("/")
    # Find "blobs" in the path and get the next part
    for i, part in enumerate(parts):
        if part == "blobs" and i + 1 < len(parts):
            return parts[i + 1]
    return None


def _find_orphaned_blobs(
    bucket: str,
    prefix: str,
    sync_tool: str,
    debug: bool,
    quiet: bool,
) -> tuple[list[str], int, int, int, list[str]]:
    """Find orphaned blobs in S3 that are not referenced by any manifest.

    Args:
        bucket: S3 bucket name.
        prefix: S3 key prefix.
        sync_tool: Either "rclone" or "aws".
        debug: If True, show debug output.
        quiet: If True, suppress progress output.

    Returns:
        Tuple of (orphaned_blob_paths, manifest_count, tagged_blob_count, total_blob_count, errors).
    """
    errors: list[str] = []

    # Choose list function based on sync tool
    if sync_tool == RCLONE_CMD:
        list_files = _list_s3_files_rclone
        get_content = _get_s3_file_content_rclone
    else:
        list_files = _list_s3_files_aws
        get_content = _get_s3_file_content_aws

    # Step 1: List all manifest files
    if not quiet and not is_json_output():
        click.echo("Scanning S3 for manifests...")

    # Use **/.magpie pattern to match hidden .magpie files in artifact directories
    manifest_paths = list_files(bucket, prefix, "**/.magpie", debug)

    # Filter to only .magpie files (AWS CLI pattern matching is different)
    manifest_paths = [p for p in manifest_paths if p.endswith(".magpie")]

    if debug:
        click.echo(f"Found {len(manifest_paths)} manifest files", err=True)

    # Step 2: Parse manifests and build set of tagged blob hashes
    if not quiet and not is_json_output():
        click.echo("Parsing manifests...")

    tagged_blobs: set[str] = set()
    for manifest_path in manifest_paths:
        content = get_content(bucket, prefix, manifest_path, debug)
        if content:
            hashes = _parse_manifest_content(content)
            tagged_blobs.update(hashes)
        else:
            errors.append(f"Failed to read manifest: {manifest_path}")

    if debug:
        click.echo(f"Found {len(tagged_blobs)} unique tagged blob hashes", err=True)

    # Step 3: List all blob files in S3
    if not quiet and not is_json_output():
        click.echo("Scanning S3 for blobs...")

    blob_paths = list_files(bucket, prefix, "*/blobs/*", debug)
    # Filter to actual blob files (not directories or other files)
    blob_paths = [p for p in blob_paths if "/blobs/" in p]

    if debug:
        click.echo(f"Found {len(blob_paths)} blob files", err=True)

    # Step 4: Find orphaned blobs (not referenced by any manifest)
    orphaned_paths: list[str] = []
    for blob_path in blob_paths:
        blob_name = _extract_blob_name_from_path(blob_path)
        if blob_name and blob_name not in tagged_blobs:
            orphaned_paths.append(blob_path)

    return orphaned_paths, len(manifest_paths), len(tagged_blobs), len(blob_paths), errors


@sync.command("gc-s3")
@click.option(
    "--dry-run",
    is_flag=True,
    default=True,
    help="Show what would be deleted without deleting (default).",
)
@click.option(
    "--execute",
    is_flag=True,
    default=False,
    help="Actually delete orphaned blobs. Required to perform deletion.",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress progress output.",
)
@click.pass_obj
def gc_s3(ctx: CTLContext, dry_run: bool, execute: bool, quiet: bool) -> None:
    """Garbage collect orphaned blobs from S3 backup.

    This command identifies and removes blobs in S3 that are not referenced
    by any manifest file. This prevents S3 storage from growing unbounded
    when local GC removes blobs that have been synced to S3.

    The command uses S3's own manifests as the source of truth, meaning it
    can run independently of local storage state.

    SAFE BY DEFAULT: This command only previews deletions unless --execute
    is explicitly provided. Always review the preview before executing.

    Examples:

        # Preview what would be deleted (default behavior)
        magpie-ctl sync gc-s3

        # Actually delete orphaned blobs
        magpie-ctl sync gc-s3 --execute

        # With explicit bucket
        MAGPIE_S3_BUCKET=my-bucket magpie-ctl sync gc-s3
    """
    settings = ctx.settings

    # If --execute is provided, disable dry_run
    if execute:
        dry_run = False

    # Check S3 configuration
    if not settings.s3_bucket:
        msg = "MAGPIE_S3_BUCKET environment variable is required"
        if is_json_output():
            output_error(ErrorCode.CONFIG_ERROR, msg)
        else:
            raise click.ClickException(msg)

    # Find sync tool
    sync_tool = _find_sync_tool()
    if not sync_tool:
        msg = "Neither rclone nor aws CLI found. Please install one of them."
        if is_json_output():
            output_error(ErrorCode.CONFIG_ERROR, msg)
        else:
            raise click.ClickException(msg)

    if ctx.debug:
        click.echo(f"Using sync tool: {sync_tool}", err=True)
        click.echo(f"S3 bucket: {settings.s3_bucket}", err=True)
        click.echo(f"S3 prefix: {settings.s3_prefix or '(none)'}", err=True)
        click.echo(f"Dry run: {dry_run}", err=True)

    # Find orphaned blobs
    orphaned_paths, manifest_count, tagged_count, total_blobs, errors = _find_orphaned_blobs(
        settings.s3_bucket,
        settings.s3_prefix,
        sync_tool,
        ctx.debug,
        quiet or is_json_output(),
    )

    # Get sizes of orphaned blobs
    if sync_tool == RCLONE_CMD:
        sizes = _get_s3_file_size_rclone(
            settings.s3_bucket, settings.s3_prefix, orphaned_paths, ctx.debug
        )
    else:
        sizes = _get_s3_file_size_aws(
            settings.s3_bucket, settings.s3_prefix, orphaned_paths, ctx.debug
        )

    total_orphaned_bytes = sum(sizes.get(p, 0) for p in orphaned_paths)

    # Delete orphaned blobs if not dry-run
    deleted_count = 0
    deleted_bytes = 0
    if not dry_run and orphaned_paths:
        if sync_tool == RCLONE_CMD:
            delete_fn = _delete_s3_file_rclone
        else:
            delete_fn = _delete_s3_file_aws

        if not quiet and not is_json_output():
            click.echo(f"Deleting {len(orphaned_paths)} orphaned blob(s)...")

        with count_progress(
            "Deleting orphaned blobs", len(orphaned_paths), quiet=quiet or is_json_output()
        ) as (progress, task_id):
            for blob_path in orphaned_paths:
                if delete_fn(settings.s3_bucket, settings.s3_prefix, blob_path, ctx.debug):
                    deleted_count += 1
                    deleted_bytes += sizes.get(blob_path, 0)
                else:
                    errors.append(f"Failed to delete: {blob_path}")

                if progress and task_id is not None:
                    progress.update(task_id, advance=1)

    # Output results
    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "dry_run": dry_run,
                    "sync_tool": sync_tool,
                    "manifests_found": manifest_count,
                    "tagged_blobs": tagged_count,
                    "total_blobs": total_blobs,
                    "orphaned_count": len(orphaned_paths),
                    "orphaned_bytes": total_orphaned_bytes,
                    "orphaned_paths": orphaned_paths if dry_run else [],
                    "deleted_count": deleted_count,
                    "deleted_bytes": deleted_bytes,
                    "errors": errors,
                },
                human_output="",
            )
        )
    else:
        click.echo("")
        if dry_run:
            click.echo("S3 Garbage Collection Preview:")
        else:
            click.echo("S3 Garbage Collection Complete:")

        click.echo(f"  Sync tool: {sync_tool}")
        click.echo(f"  Manifests found: {manifest_count}")
        click.echo(f"  Tagged blobs: {tagged_count}")
        click.echo(f"  Total blobs in S3: {total_blobs}")
        click.echo(f"  Orphaned blobs: {len(orphaned_paths)} ({format_size(total_orphaned_bytes)})")

        if dry_run:
            if orphaned_paths:
                click.echo("")
                click.echo("Orphaned blobs that would be deleted:")
                for path in orphaned_paths[:20]:  # Show first 20
                    size_str = format_size(sizes.get(path, 0))
                    click.echo(f"  - {path} ({size_str})")
                if len(orphaned_paths) > 20:
                    click.echo(f"  ... and {len(orphaned_paths) - 20} more")
                click.echo("")
                click.echo(
                    f"Would delete {len(orphaned_paths)} blob(s). Run with --execute to proceed."
                )
            else:
                click.echo("")
                click.echo("No orphaned blobs found. S3 storage is clean.")
        else:
            click.echo(f"  Deleted: {deleted_count} blob(s) ({format_size(deleted_bytes)})")

        if errors:
            click.echo("")
            click.echo("Errors:")
            for error in errors:
                click.echo(f"  - {error}", err=True)
            raise SystemExit(1)
