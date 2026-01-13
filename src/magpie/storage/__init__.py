"""Storage operations - blobs, manifests, and tags."""

from magpie.storage.blob import (
    check_blob_exists,
    get_temp_path,
    read_blob,
    store_blob,
)
from magpie.storage.cleanup import (
    CleanupStats,
    cleanup_artifact_directories,
)
from magpie.storage.exceptions import (
    ArtifactNotFoundError,
    BlobExistsError,
    HashMismatchError,
    ManifestCorruptError,
    StorageError,
)
from magpie.storage.gc import (
    BlobToDelete,
    GCResult,
    ProgressCallback,
    SymlinkFixDetail,
    get_blob_age_days,
    run_gc,
)
from magpie.storage.hash import compute_hash, short_hash
from magpie.storage.manifest import (
    Manifest,
    read_manifest,
    remove_tag,
    update_tag,
    write_manifest,
)
from magpie.storage.metadata import (
    BlobMetadata,
    read_metadata,
    update_metadata,
    write_metadata,
)
from magpie.storage.paths import (
    artifact_dir_path,
    blob_path,
    manifest_path,
    metadata_path,
)
from magpie.storage.service import ArtifactInfo, FlushResult, StorageService
from magpie.storage.symlinks import (
    create_symlink,
    reconcile_symlinks,
    remove_symlink,
)
from magpie.utils.formatting import format_size

__all__ = [
    # Storage service (main API)
    "StorageService",
    "ArtifactInfo",
    "FlushResult",
    # Blob operations
    "store_blob",
    "read_blob",
    "check_blob_exists",
    "get_temp_path",
    # Hash functions
    "compute_hash",
    "short_hash",
    # Path functions
    "artifact_dir_path",
    "blob_path",
    "manifest_path",
    "metadata_path",
    # Manifest operations
    "Manifest",
    "read_manifest",
    "write_manifest",
    "update_tag",
    "remove_tag",
    # Metadata operations
    "BlobMetadata",
    "read_metadata",
    "update_metadata",
    "write_metadata",
    # Symlink operations
    "create_symlink",
    "remove_symlink",
    "reconcile_symlinks",
    # Cleanup operations
    "CleanupStats",
    "cleanup_artifact_directories",
    # GC operations
    "GCResult",
    "BlobToDelete",
    "SymlinkFixDetail",
    "ProgressCallback",
    "run_gc",
    "get_blob_age_days",
    "format_size",
    # Exceptions
    "StorageError",
    "ArtifactNotFoundError",
    "BlobExistsError",
    "ManifestCorruptError",
    "HashMismatchError",
]
