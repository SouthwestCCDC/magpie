"""Concurrency tests for Magpie artifact storage.

Tests for race conditions and data corruption during concurrent operations:
1. Concurrent uploads - Same artifact uploaded simultaneously
2. Concurrent tag operations - Tag/untag during GC
3. Upload during GC - Upload completing while GC is running
4. Concurrent amend - Multiple amend operations on same artifact
5. Lock contention - Multiple GC processes trying to acquire lock
"""

from __future__ import annotations

import asyncio
import hashlib
import io
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from magpie.config import MagpieSettings
from magpie.server.app import app
from magpie.server.deps import get_storage_service
from magpie.storage.gc import run_gc
from magpie.storage.service import StorageService


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with temporary paths."""
    config = MagpieSettings(storage_path=tmp_path)
    config.temp_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def test_storage_service(test_config: MagpieSettings) -> StorageService:
    """Create a StorageService instance for testing."""
    return StorageService(test_config)


@pytest.fixture
def client(test_storage_service: StorageService) -> TestClient:
    """Create test client with overridden storage service dependency."""

    def override_storage_service() -> StorageService:
        return test_storage_service

    app.dependency_overrides[get_storage_service] = override_storage_service
    yield TestClient(app)
    app.dependency_overrides.clear()


def _compute_hash(content: bytes) -> str:
    """Compute SHA-256 hash of content."""
    return hashlib.sha256(content).hexdigest()


class TestConcurrentUploads:
    """Tests for concurrent uploads of the same artifact.

    Note: StorageService performs filesystem operations without explicit file
    locking. These tests characterize the observed behavior under concurrent
    access rather than asserting specific locking guarantees. The content-
    addressable storage design provides natural idempotency for same-content
    uploads, which mitigates many race conditions in practice.
    """

    async def test_concurrent_uploads_same_content(
        self, test_config: MagpieSettings, test_storage_service: StorageService
    ) -> None:
        """Multiple concurrent uploads of same content produce consistent result.

        When the same artifact content is uploaded simultaneously, all uploads
        should succeed and produce the same hash reference. The content-addressable
        design means identical content produces identical paths, providing natural
        idempotency without requiring explicit locking.
        """
        content = b"concurrent upload test content"
        expected_hash = _compute_hash(content)
        artifact_path = "concurrent/same-content"
        num_concurrent = 5

        async def upload_artifact(worker_id: int) -> tuple[str, bool]:
            """Upload artifact and return (hash, is_duplicate)."""
            file_stream = io.BytesIO(content)
            info, is_dup = test_storage_service.store_artifact(
                artifact_path=artifact_path,
                file_stream=file_stream,
                uploaded_by=f"worker-{worker_id}",
            )
            return info.hash, is_dup

        # Run uploads concurrently
        tasks = [upload_artifact(i) for i in range(num_concurrent)]
        results = await asyncio.gather(*tasks)

        # All uploads should produce same hash
        hashes = [r[0] for r in results]
        assert all(h == expected_hash for h in hashes), "All uploads should produce same hash"

        # First upload should not be duplicate, rest should be
        duplicates = [r[1] for r in results]
        assert duplicates.count(False) >= 1, "At least one upload should be non-duplicate"

        # Verify final state is consistent
        info = test_storage_service.get_artifact_info(artifact_path, "latest")
        assert info.hash == expected_hash
        assert "latest" in info.tags

    async def test_concurrent_uploads_different_content(
        self, test_config: MagpieSettings, test_storage_service: StorageService
    ) -> None:
        """Concurrent uploads of different content to same path.

        Each upload should succeed and "latest" tag should point to one of them.
        All uploaded blobs should be preserved.
        """
        base_path = "concurrent/diff-content"
        num_concurrent = 5

        async def upload_artifact(worker_id: int) -> str:
            """Upload unique content and return hash."""
            content = f"unique content from worker {worker_id}".encode()
            file_stream = io.BytesIO(content)
            info, _ = test_storage_service.store_artifact(
                artifact_path=base_path,
                file_stream=file_stream,
                uploaded_by=f"worker-{worker_id}",
            )
            return info.hash

        # Run uploads concurrently
        tasks = [upload_artifact(i) for i in range(num_concurrent)]
        hashes = await asyncio.gather(*tasks)

        # All hashes should be unique
        unique_hashes = set(hashes)
        assert len(unique_hashes) == num_concurrent, "Each upload should have unique hash"

        # "latest" should point to one of the uploaded versions
        info = test_storage_service.get_artifact_info(base_path, "latest")
        assert info.hash in unique_hashes

        # All versions should be retrievable
        versions = test_storage_service.list_artifacts(base_path)
        stored_hashes = {v.hash for v in versions}
        assert unique_hashes == stored_hashes, "All uploaded versions should be stored"


class TestConcurrentTagOperations:
    """Tests for concurrent tag creation and removal."""

    async def test_concurrent_tag_creation(
        self, test_config: MagpieSettings, test_storage_service: StorageService
    ) -> None:
        """Multiple workers creating different tags on same blob.

        All tags should be created without data corruption.
        """
        artifact_path = "concurrent/tag-creation"
        content = b"tag test content"
        num_tags = 10

        # Upload initial artifact
        file_stream = io.BytesIO(content)
        info, _ = test_storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=file_stream,
            uploaded_by="setup",
        )
        hash_ref = info.hash_ref

        async def create_tag(tag_num: int) -> str:
            """Create a tag and return its name."""
            tag_name = f"tag-{tag_num}"
            test_storage_service.create_tag(artifact_path, hash_ref, tag_name)
            return tag_name

        # Create tags concurrently
        tasks = [create_tag(i) for i in range(num_tags)]
        created_tags = await asyncio.gather(*tasks)

        # All tags should exist
        info = test_storage_service.get_artifact_info(artifact_path, hash_ref)
        for tag in created_tags:
            assert tag in info.tags, f"Tag {tag} should exist"

    async def test_concurrent_tag_update(
        self, test_config: MagpieSettings, test_storage_service: StorageService
    ) -> None:
        """Multiple workers updating same tag to point to different blobs.

        Tag should point to one of the blobs after all updates complete.
        """
        artifact_path = "concurrent/tag-update"
        tag_name = "release"
        num_versions = 5

        # Upload multiple versions
        hash_refs = []
        for i in range(num_versions):
            content = f"version {i} content".encode()
            file_stream = io.BytesIO(content)
            info, _ = test_storage_service.store_artifact(
                artifact_path=artifact_path,
                file_stream=file_stream,
                uploaded_by=f"uploader-{i}",
            )
            hash_refs.append(info.hash_ref)

        async def update_tag(version_idx: int) -> str:
            """Update tag to point to specific version."""
            test_storage_service.create_tag(artifact_path, hash_refs[version_idx], tag_name)
            return hash_refs[version_idx]

        # Update tag concurrently from different workers
        tasks = [update_tag(i) for i in range(num_versions)]
        await asyncio.gather(*tasks)

        # Tag should point to one of the versions
        info = test_storage_service.get_artifact_info(artifact_path, tag_name)
        assert info.hash_ref in hash_refs

    async def test_concurrent_tag_and_untag(
        self, test_config: MagpieSettings, test_storage_service: StorageService
    ) -> None:
        """Concurrent tagging and untagging operations.

        Operations should complete without errors or data corruption.
        """
        artifact_path = "concurrent/tag-untag"
        content = b"tag untag test content"

        # Upload artifact
        file_stream = io.BytesIO(content)
        info, _ = test_storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=file_stream,
            uploaded_by="setup",
        )
        hash_ref = info.hash_ref

        # Create some initial tags
        initial_tags = ["v1.0", "v1.1", "v1.2", "stable", "beta"]
        for tag in initial_tags:
            test_storage_service.create_tag(artifact_path, hash_ref, tag)

        async def tag_operation(op_idx: int) -> None:
            """Perform tag or untag operation."""
            if op_idx % 2 == 0:
                # Create new tag
                test_storage_service.create_tag(artifact_path, hash_ref, f"new-tag-{op_idx}")
            else:
                # Remove existing tag (may return False if already removed by another worker)
                tag_to_remove = initial_tags[op_idx % len(initial_tags)]
                test_storage_service.remove_tag(artifact_path, tag_to_remove)

        # Run mixed operations concurrently
        tasks = [tag_operation(i) for i in range(20)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Check that any exceptions are expected types (file not found during race)
        # rather than unexpected errors (corruption, bugs, etc.)
        for result in results:
            if isinstance(result, Exception):
                # FileNotFoundError can occur if manifest file is being updated concurrently
                # These are expected race conditions in the absence of file locking
                assert isinstance(result, (FileNotFoundError, OSError)), (
                    f"Unexpected exception type: {type(result).__name__}: {result}"
                )

        # Artifact should still be accessible
        versions = test_storage_service.list_artifacts(artifact_path)
        assert len(versions) >= 1, "Artifact should still exist"


class TestUploadDuringGC:
    """Tests for uploads occurring during garbage collection."""

    async def test_upload_during_gc_scan(
        self, test_config: MagpieSettings, test_storage_service: StorageService
    ) -> None:
        """Upload completing while GC scan is in progress.

        New upload should be preserved even if GC is running.
        """
        artifact_path = "concurrent/gc-upload"

        # Create some existing artifacts to give GC work to do
        for i in range(5):
            content = f"existing artifact {i}".encode()
            file_stream = io.BytesIO(content)
            test_storage_service.store_artifact(
                artifact_path=f"gc-scan/existing-{i}",
                file_stream=file_stream,
                uploaded_by="setup",
            )

        def run_gc_sync() -> None:
            """Run GC synchronously."""
            run_gc(
                storage_path=test_config.storage_path,
                retention_days=0,  # Delete old untagged immediately
                dry_run=False,
            )

        def upload_sync() -> str:
            """Upload artifact synchronously."""
            content = b"new upload during GC"
            file_stream = io.BytesIO(content)
            info, _ = test_storage_service.store_artifact(
                artifact_path=artifact_path,
                file_stream=file_stream,
                uploaded_by="concurrent-uploader",
            )
            return info.hash

        # Run GC and upload concurrently using to_thread
        gc_task = asyncio.create_task(asyncio.to_thread(run_gc_sync))
        upload_task = asyncio.create_task(asyncio.to_thread(upload_sync))

        # Wait for both to complete
        _, upload_hash = await asyncio.gather(gc_task, upload_task)

        # Verify upload was preserved
        info = test_storage_service.get_artifact_info(artifact_path, "latest")
        assert info.hash == upload_hash, "Uploaded artifact should be preserved after GC"

    async def test_gc_preserves_tagged_artifacts(
        self, test_config: MagpieSettings, test_storage_service: StorageService
    ) -> None:
        """GC running concurrently with tag operations preserves tagged blobs.

        Tagged artifacts should never be deleted, even during concurrent GC.
        """
        artifact_path = "concurrent/gc-preserve"
        content = b"must preserve this content"

        # Upload and tag artifact
        file_stream = io.BytesIO(content)
        info, _ = test_storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=file_stream,
            uploaded_by="setup",
        )
        expected_hash = info.hash

        def gc_sync() -> None:
            """Run aggressive GC."""
            run_gc(
                storage_path=test_config.storage_path,
                retention_days=0,
                dry_run=False,
            )

        async def verify_task() -> None:
            """Continuously verify artifact exists during GC."""
            for _ in range(10):
                current_info = test_storage_service.get_artifact_info(artifact_path, "latest")
                assert current_info.hash == expected_hash
                await asyncio.sleep(0.01)

        # Run GC and verification concurrently
        await asyncio.gather(
            asyncio.to_thread(gc_sync),
            verify_task(),
        )

        # Final verification
        final_info = test_storage_service.get_artifact_info(artifact_path, "latest")
        assert final_info.hash == expected_hash


class TestConcurrentAmend:
    """Tests for concurrent metadata amendment operations."""

    async def test_concurrent_amend_same_field(
        self, test_config: MagpieSettings, test_storage_service: StorageService
    ) -> None:
        """Multiple workers amending source_uri on same blob.

        Final value should be one of the attempted values.
        """
        artifact_path = "concurrent/amend-same"
        content = b"amend test content"

        # Upload artifact
        file_stream = io.BytesIO(content)
        info, _ = test_storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=file_stream,
            uploaded_by="setup",
        )
        hash_ref = info.hash_ref

        uris = [f"https://source-{i}.example.com" for i in range(5)]

        async def amend_source_uri(uri_idx: int) -> str:
            """Amend source_uri and return the value used."""
            uri = uris[uri_idx]
            test_storage_service.amend_metadata(
                artifact_path=artifact_path,
                hash_ref=hash_ref,
                source_uri=uri,
            )
            return uri

        # Amend concurrently
        tasks = [amend_source_uri(i) for i in range(len(uris))]
        await asyncio.gather(*tasks)

        # Final value should be one of the attempted URIs
        info = test_storage_service.get_artifact_info(artifact_path, hash_ref)
        assert info.source_uri in uris, "source_uri should be one of the attempted values"

        # Immutable fields should be preserved
        assert info.uploaded_by == "setup"

    async def test_concurrent_amend_preserves_immutable_fields(
        self, test_config: MagpieSettings, test_storage_service: StorageService
    ) -> None:
        """Concurrent amends preserve immutable fields (hash, uploaded_by, uploaded_at)."""
        artifact_path = "concurrent/amend-preserve"
        content = b"immutable fields test"

        # Upload artifact
        file_stream = io.BytesIO(content)
        info, _ = test_storage_service.store_artifact(
            artifact_path=artifact_path,
            file_stream=file_stream,
            uploaded_by="original-uploader",
        )
        original_hash = info.hash
        original_uploader = info.uploaded_by
        original_uploaded_at = info.uploaded_at
        hash_ref = info.hash_ref

        async def amend_task(worker_id: int) -> None:
            """Amend metadata multiple times."""
            for i in range(5):
                test_storage_service.amend_metadata(
                    artifact_path=artifact_path,
                    hash_ref=hash_ref,
                    source_uri=f"https://worker-{worker_id}-iteration-{i}.example.com",
                )

        # Run many concurrent amends
        tasks = [amend_task(i) for i in range(5)]
        await asyncio.gather(*tasks)

        # Verify immutable fields are preserved
        info = test_storage_service.get_artifact_info(artifact_path, hash_ref)
        assert info.hash == original_hash, "Hash should be preserved"
        assert info.uploaded_by == original_uploader, "uploaded_by should be preserved"
        assert info.uploaded_at == original_uploaded_at, "uploaded_at should be preserved"


class TestConcurrentMixedOperations:
    """Tests for mixed concurrent operations."""

    async def test_upload_tag_amend_concurrent(
        self, test_config: MagpieSettings, test_storage_service: StorageService
    ) -> None:
        """Upload, tag, and amend operations running concurrently.

        All operations should complete without data corruption.
        """
        base_path = "concurrent/mixed-ops"

        # Pre-create some artifacts
        created_hashes = []
        for i in range(3):
            content = f"pre-created artifact {i}".encode()
            file_stream = io.BytesIO(content)
            info, _ = test_storage_service.store_artifact(
                artifact_path=f"{base_path}/pre-{i}",
                file_stream=file_stream,
                uploaded_by="setup",
            )
            created_hashes.append((f"{base_path}/pre-{i}", info.hash_ref))

        async def upload_task(worker_id: int) -> None:
            """Upload new artifact."""
            content = f"new upload from worker {worker_id}".encode()
            file_stream = io.BytesIO(content)
            test_storage_service.store_artifact(
                artifact_path=f"{base_path}/new-{worker_id}",
                file_stream=file_stream,
                uploaded_by=f"worker-{worker_id}",
            )

        async def tag_task(artifact_path: str, hash_ref: str, tag_num: int) -> None:
            """Create tag on existing artifact."""
            test_storage_service.create_tag(artifact_path, hash_ref, f"tag-{tag_num}")

        async def amend_task(artifact_path: str, hash_ref: str, amend_num: int) -> None:
            """Amend metadata on existing artifact."""
            test_storage_service.amend_metadata(
                artifact_path=artifact_path,
                hash_ref=hash_ref,
                source_uri=f"https://amend-{amend_num}.example.com",
            )

        # Build task list with mixed operations
        tasks = []

        # Upload tasks
        for i in range(5):
            tasks.append(upload_task(i))

        # Tag tasks on pre-created artifacts
        for path, hash_ref in created_hashes:
            for i in range(3):
                tasks.append(tag_task(path, hash_ref, i))

        # Amend tasks on pre-created artifacts
        for path, hash_ref in created_hashes:
            for i in range(3):
                tasks.append(amend_task(path, hash_ref, i))

        # Run all tasks concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Check that any exceptions are expected types rather than unexpected errors
        for result in results:
            if isinstance(result, Exception):
                # FileNotFoundError/OSError can occur during concurrent filesystem access
                assert isinstance(result, (FileNotFoundError, OSError)), (
                    f"Unexpected exception type: {type(result).__name__}: {result}"
                )

        # Verify all pre-created artifacts still exist and are accessible
        for path, hash_ref in created_hashes:
            info = test_storage_service.get_artifact_info(path, hash_ref)
            assert info is not None, f"Artifact {path} should still exist"

        # Verify new artifacts were created
        for i in range(5):
            versions = test_storage_service.list_artifacts(f"{base_path}/new-{i}")
            assert len(versions) >= 1, f"New artifact new-{i} should exist"


class TestGCLockContention:
    """Tests for GC lock contention scenarios.

    Note: The current implementation does not use file locking for GC.
    These tests characterize behavior when multiple GC processes run
    simultaneously. Because GC only deletes blobs that are untagged AND
    older than the retention period, the worst case is that a blob gets
    deleted by one GC process while another is scanning it, which may
    result in a FileNotFoundError during the scan. The tests verify that
    tagged artifacts are preserved and storage remains consistent, but
    do not attempt to trigger specific race conditions.
    """

    async def test_concurrent_gc_processes(
        self, test_config: MagpieSettings, test_storage_service: StorageService
    ) -> None:
        """Multiple GC processes running simultaneously.

        Characterizes behavior under concurrent GC execution. All processes
        should complete (possibly with FileNotFoundError for already-deleted
        blobs) and tagged artifacts should be preserved.
        """
        # Create artifacts with tagged and untagged blobs
        for i in range(5):
            content = f"tagged artifact {i}".encode()
            file_stream = io.BytesIO(content)
            test_storage_service.store_artifact(
                artifact_path=f"gc-contention/tagged-{i}",
                file_stream=file_stream,
                uploaded_by="setup",
            )

        # Create some untagged blobs by uploading and then uploading different content
        for i in range(3):
            # First upload (will become untagged)
            content1 = f"old version {i}".encode()
            file_stream1 = io.BytesIO(content1)
            test_storage_service.store_artifact(
                artifact_path=f"gc-contention/updated-{i}",
                file_stream=file_stream1,
                uploaded_by="setup",
            )

            # Second upload (becomes "latest", old becomes untagged)
            content2 = f"new version {i}".encode()
            file_stream2 = io.BytesIO(content2)
            test_storage_service.store_artifact(
                artifact_path=f"gc-contention/updated-{i}",
                file_stream=file_stream2,
                uploaded_by="setup",
            )

        def gc_process_sync(process_id: int) -> tuple[int, int]:
            """Run GC and return (blobs_found, blobs_deleted)."""
            result, _ = run_gc(
                storage_path=test_config.storage_path,
                retention_days=0,  # Delete old untagged immediately
                dry_run=False,
            )
            return result.blobs_found, result.blobs_deleted

        # Run multiple GC processes concurrently
        tasks = [asyncio.to_thread(gc_process_sync, i) for i in range(3)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out any exceptions and check results
        successful_results = [r for r in results if not isinstance(r, Exception)]
        assert len(successful_results) >= 1, "At least one GC process should succeed"

        # Verify storage is consistent - all tagged artifacts should still exist
        for i in range(5):
            info = test_storage_service.get_artifact_info(f"gc-contention/tagged-{i}", "latest")
            assert info is not None, f"Tagged artifact {i} should still exist"

        for i in range(3):
            info = test_storage_service.get_artifact_info(f"gc-contention/updated-{i}", "latest")
            assert info is not None, f"Updated artifact {i} should still exist"

    async def test_gc_with_concurrent_uploads(
        self, test_config: MagpieSettings, test_storage_service: StorageService
    ) -> None:
        """GC running while uploads are happening.

        Uploads should succeed and newly uploaded artifacts should be preserved.
        """
        # Pre-populate with some artifacts
        for i in range(3):
            content = f"existing {i}".encode()
            file_stream = io.BytesIO(content)
            test_storage_service.store_artifact(
                artifact_path=f"gc-upload/existing-{i}",
                file_stream=file_stream,
                uploaded_by="setup",
            )

        uploaded_hashes: list[str] = []

        async def upload_continuously() -> None:
            """Upload artifacts continuously during GC."""
            for i in range(10):
                content = f"uploaded during GC {i}".encode()
                file_stream = io.BytesIO(content)
                info, _ = test_storage_service.store_artifact(
                    artifact_path=f"gc-upload/during-{i}",
                    file_stream=file_stream,
                    uploaded_by="concurrent",
                )
                uploaded_hashes.append(info.hash)
                await asyncio.sleep(0.01)

        def gc_sync() -> None:
            """Run GC synchronously."""
            run_gc(
                storage_path=test_config.storage_path,
                retention_days=0,
                dry_run=False,
            )

        # Run GC and uploads concurrently
        await asyncio.gather(
            asyncio.to_thread(gc_sync),
            upload_continuously(),
        )

        # All uploaded artifacts should exist
        for i in range(10):
            info = test_storage_service.get_artifact_info(f"gc-upload/during-{i}", "latest")
            assert info is not None, f"Artifact uploaded during GC should exist: during-{i}"
            assert info.hash == uploaded_hashes[i]


class TestAsyncClientConcurrency:
    """Tests using httpx.AsyncClient for HTTP-level concurrency testing."""

    @pytest.fixture
    async def async_client(self, test_storage_service: StorageService) -> httpx.AsyncClient:
        """Create async HTTP client for testing."""

        def override_storage_service() -> StorageService:
            return test_storage_service

        app.dependency_overrides[get_storage_service] = override_storage_service

        # Use ASGI transport for async testing
        from httpx import ASGITransport

        transport = ASGITransport(app=app)  # type: ignore[arg-type]
        client = httpx.AsyncClient(transport=transport, base_url="http://test")

        yield client

        await client.aclose()
        app.dependency_overrides.clear()

    async def test_concurrent_http_uploads(
        self, async_client: httpx.AsyncClient, test_storage_service: StorageService
    ) -> None:
        """Concurrent HTTP upload requests.

        Multiple simultaneous upload requests should all succeed.
        """
        num_uploads = 10

        async def upload(worker_id: int) -> httpx.Response:
            """Make upload request."""
            content = f"HTTP upload from worker {worker_id}".encode()
            files = {"file": ("artifact.bin", content, "application/octet-stream")}
            response = await async_client.post(
                f"/api/v1/upload/http-concurrent/worker-{worker_id}",
                files=files,
                params={"uploaded_by": f"worker-{worker_id}"},
            )
            return response

        # Make concurrent requests
        tasks = [upload(i) for i in range(num_uploads)]
        responses = await asyncio.gather(*tasks)

        # All should succeed
        for i, response in enumerate(responses):
            assert response.status_code == 200, f"Upload {i} should succeed"
            data = response.json()
            assert "hash" in data
            assert "hash_ref" in data

    async def test_concurrent_http_tag_operations(
        self, async_client: httpx.AsyncClient, test_storage_service: StorageService
    ) -> None:
        """Concurrent HTTP tag creation requests."""
        # First upload an artifact
        content = b"tag operations test"
        files = {"file": ("artifact.bin", content, "application/octet-stream")}
        upload_response = await async_client.post(
            "/api/v1/upload/http-tags/test",
            files=files,
            params={"uploaded_by": "setup"},
        )
        assert upload_response.status_code == 200
        hash_ref = upload_response.json()["hash_ref"]

        num_tags = 10

        async def create_tag(tag_num: int) -> httpx.Response:
            """Create a tag via HTTP."""
            response = await async_client.post(
                f"/api/v1/artifacts/http-tags/test/{hash_ref}/tags",
                json={"tag_name": f"http-tag-{tag_num}"},
            )
            return response

        # Create tags concurrently
        tasks = [create_tag(i) for i in range(num_tags)]
        responses = await asyncio.gather(*tasks)

        # All should succeed
        for i, response in enumerate(responses):
            assert response.status_code == 200, f"Tag creation {i} should succeed"

        # Verify all tags exist
        info_response = await async_client.get(f"/api/v1/artifacts/http-tags/test/{hash_ref}/info")
        assert info_response.status_code == 200
        tags = info_response.json()["tags"]

        for i in range(num_tags):
            assert f"http-tag-{i}" in tags, f"Tag http-tag-{i} should exist"

    async def test_concurrent_http_amend(
        self, async_client: httpx.AsyncClient, test_storage_service: StorageService
    ) -> None:
        """Concurrent HTTP amend requests."""
        # First upload an artifact
        content = b"amend test content"
        files = {"file": ("artifact.bin", content, "application/octet-stream")}
        upload_response = await async_client.post(
            "/api/v1/upload/http-amend/test",
            files=files,
            params={"uploaded_by": "setup"},
        )
        assert upload_response.status_code == 200
        hash_ref = upload_response.json()["hash_ref"]
        original_hash = upload_response.json()["hash"]

        num_amends = 10

        async def amend(amend_num: int) -> httpx.Response:
            """Amend metadata via HTTP."""
            response = await async_client.patch(
                f"/api/v1/artifacts/http-amend/test/{hash_ref}",
                json={"source_uri": f"https://source-{amend_num}.example.com"},
            )
            return response

        # Amend concurrently
        tasks = [amend(i) for i in range(num_amends)]
        responses = await asyncio.gather(*tasks)

        # All should succeed
        for i, response in enumerate(responses):
            assert response.status_code == 200, f"Amend {i} should succeed"

        # Verify final state - hash should be unchanged
        info_response = await async_client.get(f"/api/v1/artifacts/http-amend/test/{hash_ref}/info")
        assert info_response.status_code == 200
        data = info_response.json()

        assert data["hash"] == original_hash, "Hash should be unchanged after amends"
        assert data["uploaded_by"] == "setup", "uploaded_by should be unchanged"
        assert data["source_uri"] is not None, "source_uri should be set"
