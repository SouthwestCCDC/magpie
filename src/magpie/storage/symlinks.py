"""Symlink management for tag-based blob access."""

from __future__ import annotations

from pathlib import Path

from magpie.storage.manifest import Manifest


def create_symlink(artifact_dir: Path, tag_name: str, hash_ref: str) -> None:
    """Create or update a symlink for a tag.

    Creates a symlink at artifact_dir/{tag_name} pointing to the blob
    identified by hash_ref. Uses relative path as target.

    Args:
        artifact_dir: Path to artifact directory.
        tag_name: Name of the tag (becomes symlink name).
        hash_ref: Hash reference to link to (with or without @ prefix).
    """
    symlink_path = artifact_dir / tag_name

    # Strip @ prefix if present for the target path
    target_name = hash_ref.lstrip("@")
    # Use relative path: blobs/{hash}
    target = Path("blobs") / target_name

    # Remove existing symlink if present (atomic update)
    if symlink_path.is_symlink():
        symlink_path.unlink()

    # Create parent directory if needed
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # Create symlink with relative target
    symlink_path.symlink_to(target)


def remove_symlink(artifact_dir: Path, tag_name: str) -> None:
    """Remove a symlink for a tag.

    Removes the symlink at artifact_dir/{tag_name} if it exists.
    No-op if symlink doesn't exist.

    Args:
        artifact_dir: Path to artifact directory.
        tag_name: Name of the tag (symlink name).
    """
    symlink_path = artifact_dir / tag_name

    if symlink_path.is_symlink():
        symlink_path.unlink()


def reconcile_symlinks(artifact_dir: Path, manifest: Manifest) -> None:
    """Reconcile symlinks to match manifest tags exactly.

    Creates missing symlinks for tags in manifest, removes orphan symlinks
    that aren't in manifest. Only affects symlinks, not regular files.

    Args:
        artifact_dir: Path to artifact directory.
        manifest: Manifest containing authoritative tag mappings.
    """
    # Get set of expected tag names from manifest
    expected_tags = set(manifest.tags.keys())

    # Find existing symlinks in artifact directory (excluding subdirectories)
    existing_symlinks: set[str] = set()
    if artifact_dir.exists():
        for item in artifact_dir.iterdir():
            if item.is_symlink():
                existing_symlinks.add(item.name)

    # Create missing symlinks
    missing_tags = expected_tags - existing_symlinks
    for tag_name in missing_tags:
        hash_ref = manifest.tags[tag_name]
        create_symlink(artifact_dir, tag_name, hash_ref)

    # Remove orphan symlinks (symlinks not in manifest)
    orphan_symlinks = existing_symlinks - expected_tags
    for tag_name in orphan_symlinks:
        remove_symlink(artifact_dir, tag_name)

    # Update existing symlinks that point to wrong target
    for tag_name in expected_tags & existing_symlinks:
        symlink_path = artifact_dir / tag_name
        expected_target = Path("blobs") / manifest.tags[tag_name].lstrip("@")

        # Check if current target matches expected
        try:
            current_target = symlink_path.readlink()
            if current_target != expected_target:
                create_symlink(artifact_dir, tag_name, manifest.tags[tag_name])
        except OSError:
            # If we can't read the symlink, recreate it
            create_symlink(artifact_dir, tag_name, manifest.tags[tag_name])
