"""Symlink management for tag-based blob access."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from magpie.storage.manifest import Manifest
from magpie.storage.paths import canonical_hash_name, resolve_blob_name


@dataclass
class ReconcileStats:
    """Statistics from symlink reconciliation operation.

    Attributes:
        checked: Number of symlinks checked.
        created: Number of symlinks created.
        removed: Number of orphan symlinks removed.
        updated: Number of symlinks updated (pointing to wrong target).
        created_tags: List of tag names that were created.
        removed_tags: List of tag names that were removed.
        updated_tags: List of tag names that were updated.
    """

    checked: int = 0
    created: int = 0
    removed: int = 0
    updated: int = 0
    created_tags: list[str] = field(default_factory=list)
    removed_tags: list[str] = field(default_factory=list)
    updated_tags: list[str] = field(default_factory=list)

    @property
    def fixed(self) -> int:
        """Total number of symlinks fixed (created + removed + updated)."""
        return self.created + self.removed + self.updated


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

    # Manifests store full hashes; blobs are stored under a truncated name,
    # so point at the name the blob actually has on disk.
    target = blob_link_target(artifact_dir, hash_ref)

    # Remove existing symlink if present (atomic update)
    if symlink_path.is_symlink():
        symlink_path.unlink()

    # Create parent directory if needed
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # Create symlink with relative target
    symlink_path.symlink_to(target)


def blob_link_target(artifact_dir: Path, hash_ref: str) -> Path:
    """Get the relative symlink target for a hash reference.

    Shared by symlink creation and reconciliation so both agree on the
    target even when the blob is stored under a filename written by an
    earlier release (a narrower hash prefix).

    Args:
        artifact_dir: Path to artifact directory.
        hash_ref: Hash reference to link to (with or without @ prefix).

    Returns:
        Relative path of the form ``blobs/{hash_name}``.
    """
    name = resolve_blob_name(artifact_dir, hash_ref) or canonical_hash_name(hash_ref)
    return Path("blobs") / name


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


def reconcile_symlinks(artifact_dir: Path, manifest: Manifest) -> ReconcileStats:
    """Reconcile symlinks to match manifest tags exactly.

    Creates missing symlinks for tags in manifest, removes orphan symlinks
    that aren't in manifest. Only affects symlinks, not regular files.

    Args:
        artifact_dir: Path to artifact directory.
        manifest: Manifest containing authoritative tag mappings.

    Returns:
        ReconcileStats with counts of checked/created/removed/updated symlinks.
    """
    stats = ReconcileStats()

    # Get set of expected tag names from manifest
    expected_tags = set(manifest.tags.keys())

    # Find existing symlinks in artifact directory (excluding subdirectories)
    existing_symlinks: set[str] = set()
    if artifact_dir.exists():
        for item in artifact_dir.iterdir():
            if item.is_symlink():
                existing_symlinks.add(item.name)

    # Total symlinks to check = expected tags + orphan symlinks
    stats.checked = len(expected_tags) + len(existing_symlinks - expected_tags)

    # Create missing symlinks
    missing_tags = expected_tags - existing_symlinks
    for tag_name in sorted(missing_tags):
        hash_ref = manifest.tags[tag_name]
        create_symlink(artifact_dir, tag_name, hash_ref)
        stats.created += 1
        stats.created_tags.append(tag_name)

    # Remove orphan symlinks (symlinks not in manifest)
    orphan_symlinks = existing_symlinks - expected_tags
    for tag_name in sorted(orphan_symlinks):
        remove_symlink(artifact_dir, tag_name)
        stats.removed += 1
        stats.removed_tags.append(tag_name)

    # Update existing symlinks that point to wrong target
    for tag_name in sorted(expected_tags & existing_symlinks):
        symlink_path = artifact_dir / tag_name
        expected_target = blob_link_target(artifact_dir, manifest.tags[tag_name])

        # Check if current target matches expected
        try:
            current_target = symlink_path.readlink()
            if current_target != expected_target:
                create_symlink(artifact_dir, tag_name, manifest.tags[tag_name])
                stats.updated += 1
                stats.updated_tags.append(tag_name)
        except OSError:
            # If we can't read the symlink, recreate it
            create_symlink(artifact_dir, tag_name, manifest.tags[tag_name])
            stats.updated += 1
            stats.updated_tags.append(tag_name)

    return stats
