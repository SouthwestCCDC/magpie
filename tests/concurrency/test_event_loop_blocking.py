"""Tests proving async route handlers don't block the event loop on storage IO.

The upload and list/info handlers call the synchronous StorageService, whose
filesystem work (nesting rglob, blob move, manifest writes, metadata iterdir)
can take a long time on large artifact trees. Those calls must run in the
threadpool, not inline on the event loop.

Each test gates the storage call on a threading.Event so the "slow" operation
is held open deterministically (no wall-clock size heuristics), then asserts a
cheap request is still served while the storage call is in flight. If the
storage call ran inline on the event loop, the cheap request could not be
served and the awaits below would time out.

AI-assisted: Generated with Devin.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import AsyncIterator, Iterator

import httpx
import pytest

from magpie.config import MagpieSettings
from magpie.server.app import app
from magpie.server.deps import get_magpie_settings, get_storage_service
from magpie.server.routes import upload as upload_routes
from magpie.storage.service import ArtifactInfo, StorageService

# Generous upper bound: these awaits complete in milliseconds when the event
# loop is free, and only elapse if the loop is blocked (the regression).
LOOP_RESPONSIVE_TIMEOUT = 10.0

# Upper bound for how long a gated storage call waits to be released. Only hit
# if the test itself fails before releasing the gate.
GATE_TIMEOUT = 10.0


class GatedStorageService(StorageService):
    """StorageService whose hot-path calls block until explicitly released.

    ``started`` is set once the storage call is entered; the call then blocks
    until ``release`` is set. This makes a storage operation arbitrarily slow
    without depending on artifact size or timing.
    """

    def __init__(self, config: MagpieSettings) -> None:
        super().__init__(config)
        self.started = threading.Event()
        self.release = threading.Event()
        self.second_store_started = threading.Event()
        self._store_entry_lock = threading.Lock()
        self._store_calls = 0

    def _gate(self) -> None:
        self.started.set()
        self.release.wait(timeout=GATE_TIMEOUT)

    def store_artifact_from_temp(self, *args, **kwargs) -> tuple[ArtifactInfo, bool]:
        """Gate the upload write path."""
        with self._store_entry_lock:
            self._store_calls += 1
            if self._store_calls == 2:
                self.second_store_started.set()
        self._gate()
        return super().store_artifact_from_temp(*args, **kwargs)

    def list_artifacts(self, *args, **kwargs) -> list[ArtifactInfo]:
        """Gate the version listing path."""
        self._gate()
        return super().list_artifacts(*args, **kwargs)

    def list_artifact_paths(self, *args, **kwargs) -> list[str]:
        """Gate the path listing path."""
        self._gate()
        return super().list_artifact_paths(*args, **kwargs)

    def get_artifact_info(self, *args, **kwargs) -> ArtifactInfo:
        """Gate the info path."""
        self._gate()
        return super().get_artifact_info(*args, **kwargs)


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with temporary paths."""
    config = MagpieSettings(storage_path=tmp_path, database_path=tmp_path / "magpie.db")
    config.temp_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def gated_storage_service(test_config: MagpieSettings) -> GatedStorageService:
    """Storage service whose hot-path calls can be held open on demand."""
    return GatedStorageService(test_config)


@pytest.fixture
def overridden_app(
    gated_storage_service: GatedStorageService, test_config: MagpieSettings
) -> Iterator[None]:
    """Point the app at the gated storage service, restoring prior overrides."""
    prior_storage = app.dependency_overrides.get(get_storage_service)
    prior_settings = app.dependency_overrides.get(get_magpie_settings)
    app.dependency_overrides[get_storage_service] = lambda: gated_storage_service
    app.dependency_overrides[get_magpie_settings] = lambda: test_config
    try:
        yield
    finally:
        if prior_storage is None:
            app.dependency_overrides.pop(get_storage_service, None)
        else:
            app.dependency_overrides[get_storage_service] = prior_storage
        if prior_settings is None:
            app.dependency_overrides.pop(get_magpie_settings, None)
        else:
            app.dependency_overrides[get_magpie_settings] = prior_settings


@pytest.fixture
async def http_client(overridden_app: None) -> AsyncIterator[httpx.AsyncClient]:
    """Async client bound to the app via ASGI transport."""
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def second_finalization_submitted(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    """Signal once a second upload has submitted finalization to the threadpool."""
    original = upload_routes.run_in_threadpool
    submitted = asyncio.Event()
    calls = 0

    async def tracked_run_in_threadpool(func, *args, **kwargs):
        nonlocal calls
        if func is upload_routes._store_artifact_serialized:
            calls += 1
            if calls == 2:
                submitted.set()
        return await original(func, *args, **kwargs)

    monkeypatch.setattr(upload_routes, "run_in_threadpool", tracked_run_in_threadpool)
    return submitted


async def _assert_loop_responsive_while(
    http_client: httpx.AsyncClient,
    gated_storage_service: GatedStorageService,
    slow_request: asyncio.Task[httpx.Response],
) -> httpx.Response:
    """Assert a cheap request is served while ``slow_request`` is mid-storage-call.

    Waits for the gated storage call to be entered, issues a cheap request that
    touches no storage, releases the gate, and returns the slow response.
    """
    try:
        # Waiting on a threading.Event from the loop only works if the loop is
        # free, which is the property under test.
        await asyncio.wait_for(
            asyncio.to_thread(gated_storage_service.started.wait, LOOP_RESPONSIVE_TIMEOUT),
            timeout=LOOP_RESPONSIVE_TIMEOUT,
        )
        assert gated_storage_service.started.is_set(), "Storage call was never reached"
        assert not slow_request.done(), "Storage call should still be in flight"

        health = await asyncio.wait_for(http_client.get("/health"), timeout=LOOP_RESPONSIVE_TIMEOUT)
        assert health.status_code == 200, "Cheap request should be served promptly"
        assert not slow_request.done(), "Storage call should still be in flight"
    finally:
        gated_storage_service.release.set()

    return await asyncio.wait_for(slow_request, timeout=LOOP_RESPONSIVE_TIMEOUT)


class TestEventLoopNotBlockedByStorageIO:
    """Hot-path handlers must offload synchronous storage IO to the threadpool."""

    async def test_upload_does_not_block_event_loop(
        self, http_client: httpx.AsyncClient, gated_storage_service: GatedStorageService
    ) -> None:
        """A slow store_artifact_from_temp() doesn't stall other requests."""
        files = {"file": ("artifact.bin", b"slow upload content", "application/octet-stream")}
        slow_request = asyncio.create_task(
            http_client.post("/api/v1/upload/slow/upload", files=files)
        )

        response = await _assert_loop_responsive_while(
            http_client, gated_storage_service, slow_request
        )

        assert response.status_code == 200
        assert response.json()["artifact_path"] == "slow/upload"

    async def test_upload_finalization_is_serialized(
        self,
        http_client: httpx.AsyncClient,
        gated_storage_service: GatedStorageService,
        second_finalization_submitted: asyncio.Event,
    ) -> None:
        """Concurrent uploads do not enter finalization together."""
        first_request = asyncio.create_task(
            http_client.post(
                "/api/v1/upload/serial/first",
                files={"file": ("first.bin", b"first upload", "application/octet-stream")},
            )
        )
        second_request: asyncio.Task[httpx.Response] | None = None
        try:
            first_started = await asyncio.wait_for(
                asyncio.to_thread(gated_storage_service.started.wait, LOOP_RESPONSIVE_TIMEOUT),
                timeout=LOOP_RESPONSIVE_TIMEOUT,
            )
            assert first_started, "First upload never entered storage finalization"

            second_request = asyncio.create_task(
                http_client.post(
                    "/api/v1/upload/serial/second",
                    files={"file": ("second.bin", b"second upload", "application/octet-stream")},
                )
            )
            await asyncio.wait_for(
                second_finalization_submitted.wait(), timeout=LOOP_RESPONSIVE_TIMEOUT
            )
            second_entered = await asyncio.to_thread(
                gated_storage_service.second_store_started.wait, 0.25
            )
            assert not second_entered, "Second upload entered while the first was gated"

            health = await asyncio.wait_for(
                http_client.get("/health"), timeout=LOOP_RESPONSIVE_TIMEOUT
            )
            assert health.status_code == 200
        finally:
            gated_storage_service.release.set()
            requests = [first_request]
            if second_request is not None:
                requests.append(second_request)
            responses = await asyncio.gather(*requests, return_exceptions=True)

        assert all(isinstance(response, httpx.Response) for response in responses), responses
        assert [response.status_code for response in responses] == [200, 200]

    async def test_concurrent_nested_uploads_leave_one_artifact(
        self,
        http_client: httpx.AsyncClient,
        gated_storage_service: GatedStorageService,
        second_finalization_submitted: asyncio.Event,
    ) -> None:
        """Serialized finalization preserves artifact nesting checks."""
        parent_request = asyncio.create_task(
            http_client.post(
                "/api/v1/upload/nest/a",
                files={"file": ("parent.bin", b"parent upload", "application/octet-stream")},
            )
        )
        child_request: asyncio.Task[httpx.Response] | None = None
        try:
            first_started = await asyncio.wait_for(
                asyncio.to_thread(gated_storage_service.started.wait, LOOP_RESPONSIVE_TIMEOUT),
                timeout=LOOP_RESPONSIVE_TIMEOUT,
            )
            assert first_started, "First upload never entered storage finalization"

            child_request = asyncio.create_task(
                http_client.post(
                    "/api/v1/upload/nest/a/b",
                    files={"file": ("child.bin", b"child upload", "application/octet-stream")},
                )
            )
            await asyncio.wait_for(
                second_finalization_submitted.wait(), timeout=LOOP_RESPONSIVE_TIMEOUT
            )
            second_entered = await asyncio.to_thread(
                gated_storage_service.second_store_started.wait, 0.25
            )
            assert not second_entered, "Nested upload entered while the first was gated"
        finally:
            gated_storage_service.release.set()
            requests = [parent_request]
            if child_request is not None:
                requests.append(child_request)
            responses = await asyncio.gather(*requests, return_exceptions=True)

        assert all(isinstance(response, httpx.Response) for response in responses), responses
        assert sorted(response.status_code for response in responses) == [200, 400]
        rejected = next(response for response in responses if response.status_code == 400)
        assert rejected.json()["error"] == "InvalidArtifactPathError"
        assert "nested under existing artifact" in rejected.json()["message"]

    async def test_list_artifacts_does_not_block_event_loop(
        self, http_client: httpx.AsyncClient, gated_storage_service: GatedStorageService
    ) -> None:
        """A slow list_artifacts() doesn't stall other requests."""
        slow_request = asyncio.create_task(http_client.get("/api/v1/artifacts/slow/listing"))

        response = await _assert_loop_responsive_while(
            http_client, gated_storage_service, slow_request
        )

        assert response.status_code == 200
        assert response.json() == {"artifact_path": "slow/listing", "versions": []}

    async def test_list_artifact_paths_does_not_block_event_loop(
        self, http_client: httpx.AsyncClient, gated_storage_service: GatedStorageService
    ) -> None:
        """A slow list_artifact_paths() doesn't stall other requests."""
        slow_request = asyncio.create_task(http_client.get("/api/v1/artifacts"))

        response = await _assert_loop_responsive_while(
            http_client, gated_storage_service, slow_request
        )

        assert response.status_code == 200
        assert response.json() == {"paths": []}

    async def test_get_artifact_info_does_not_block_event_loop(
        self, http_client: httpx.AsyncClient, gated_storage_service: GatedStorageService
    ) -> None:
        """A slow get_artifact_info() doesn't stall other requests.

        The artifact doesn't exist, so the gated call still raises
        ArtifactNotFoundError after the gate is released; the 404 translation
        must survive being raised from a worker thread.
        """
        slow_request = asyncio.create_task(
            http_client.get("/api/v1/artifacts/slow/info/latest/info")
        )

        response = await _assert_loop_responsive_while(
            http_client, gated_storage_service, slow_request
        )

        assert response.status_code == 404
