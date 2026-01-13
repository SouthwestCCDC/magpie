"""Push command for uploading artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Callable

import click

if TYPE_CHECKING:
    from rich.progress import Progress, TaskID

from magpie.cli import CLIContext
from magpie.cli.errors import handle_http_error
from magpie.cli.formatting import (
    CommandResult,
    ErrorCode,
    http_status_to_error_code,
    is_json_output,
    output_error,
    output_result,
)
from magpie.cli.progress import transfer_progress
from magpie.storage.exceptions import InvalidArtifactPathError
from magpie.storage.paths import normalize_artifact_path


class ProgressFileWrapper:
    """File wrapper that reports read progress to rich progress bar."""

    def __init__(
        self,
        file: BinaryIO,
        total_size: int,
        progress: "Progress | None",
        task_id: "TaskID | None",
        on_upload_complete: Callable[[], None] | None = None,
    ) -> None:
        self._file = file
        self._total_size = total_size
        self._progress = progress
        self._task_id = task_id
        self._on_upload_complete = on_upload_complete
        self._bytes_read = 0
        self._upload_complete_fired = False

    def read(self, size: int = -1) -> bytes:
        """Read bytes and update progress."""
        data = self._file.read(size)
        if self._progress is not None and self._task_id is not None:
            self._progress.update(self._task_id, advance=len(data))

        self._bytes_read += len(data)

        # Fire callback when all bytes have been read (upload complete)
        if (
            not self._upload_complete_fired
            and self._bytes_read >= self._total_size
            and self._on_upload_complete is not None
        ):
            self._upload_complete_fired = True
            self._on_upload_complete()

        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        """Seek in file (needed for multipart encoding).

        Resets the bytes read counter to match the new file position,
        ensuring accurate tracking even if httpx seeks back to re-read.
        """
        result = self._file.seek(offset, whence)
        # Update _bytes_read to match the new file position
        self._bytes_read = self._file.tell()
        return result

    def tell(self) -> int:
        """Return current position."""
        return self._file.tell()


def _show_processing_status(
    progress: "Progress | None", upload_task_id: "TaskID | None"
) -> "TaskID | None":
    """Update progress display to show processing status.

    Marks the upload task as finished and adds a new indeterminate task
    to show a spinner while waiting for server response.

    Returns:
        The processing task ID if created, None otherwise.
    """
    if progress is not None and upload_task_id is not None:
        # Mark the upload task as finished (hides the progress bar)
        progress.update(upload_task_id, visible=False)
        # Add a new indeterminate task for processing phase
        processing_task_id = progress.add_task("Processing", total=None)
        return processing_task_id
    return None


@click.command()
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--to", "artifact_path", required=True, help="Artifact path on server.")
@click.option("--source-uri", help="Source URI for provenance tracking.")
@click.option("--no-latest", is_flag=True, help="Don't auto-tag as latest.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress progress output.")
@click.pass_obj
def push(
    ctx: CLIContext,
    file: Path,
    artifact_path: str,
    source_uri: str | None,
    no_latest: bool,
    quiet: bool,
) -> None:
    """Upload an artifact to the server.

    FILE is the local file path to upload.

    Examples:

        magpie push myfile.tar.gz --to images/ubuntu

        magpie push build.zip --to builds/app --source-uri git://repo@v1.0
    """
    if not ctx.server:
        msg = "No server configured. Use --server or set MAGPIE_SERVER."
        if is_json_output():
            output_error(ErrorCode.CONFIG_ERROR, msg)
            return  # output_error never returns, but explicit for clarity
        raise click.ClickException(msg)

    # Normalize artifact path
    try:
        artifact_path = normalize_artifact_path(artifact_path)
    except InvalidArtifactPathError as e:
        if is_json_output():
            output_error(ErrorCode.VALIDATION_ERROR, str(e))
            return  # output_error never returns, but explicit for clarity
        raise click.ClickException(str(e))

    # Build query params
    params: dict[str, str] = {}
    if source_uri:
        params["source_uri"] = source_uri
    if no_latest:
        params["no_latest"] = "true"

    file_size = file.stat().st_size

    if ctx.debug:
        click.echo(f"Uploading {file} ({file_size} bytes) to {artifact_path}...", err=True)

    # Suppress progress output in JSON mode
    quiet_mode = quiet or is_json_output()

    # Upload file with progress
    with transfer_progress("Uploading", file_size, quiet=quiet_mode) as (progress, task_id):
        with ctx.get_client() as client:
            with file.open("rb") as f:
                # Container for processing task ID (mutable to allow callback to store it)
                processing_task: list["TaskID | None"] = [None]

                # Create callback to update display when upload completes
                def on_complete() -> None:
                    processing_task[0] = _show_processing_status(progress, task_id)

                wrapped_file = ProgressFileWrapper(
                    f, file_size, progress, task_id, on_upload_complete=on_complete
                )
                files = {"file": (file.name, wrapped_file, "application/octet-stream")}

                response = client.post(
                    f"/api/v1/upload/{artifact_path}",
                    files=files,
                    params=params if params else None,
                )

                # Hide processing indicator now that we have a response
                if progress is not None and processing_task[0] is not None:
                    progress.update(processing_task[0], visible=False)

            if response.status_code != 200:
                if is_json_output():
                    try:
                        detail = response.json().get("detail", response.text)
                    except (json.JSONDecodeError, ValueError, KeyError):
                        detail = response.text
                    output_error(http_status_to_error_code(response.status_code), detail)
                    return  # output_error never returns, but explicit for clarity
                handle_http_error(response, "Upload", ctx.token)

            data = response.json()

    # Determine download URL
    if not no_latest:
        download_url = f"{ctx.server}/artifacts/{artifact_path}/latest"
        tags = ["latest"]
    else:
        download_url = f"{ctx.server}{data['download_url']}"
        tags = []

    # JSON output
    if is_json_output():
        output_result(
            CommandResult(
                data={
                    "artifact": artifact_path,
                    "version": data["hash_ref"],
                    "hash": data["hash"],
                    "size": file_size,
                    "tags": tags,
                    "url": download_url,
                    "is_duplicate": data.get("is_duplicate", False),
                },
                human_output="",  # Not used for JSON
            )
        )
        return

    # Human output
    if data.get("is_duplicate"):
        click.echo(f"Duplicate: {data['hash']}")
    else:
        click.echo(f"Uploaded: {data['hash']}")

    click.echo(f"Hash ref: {data['hash_ref']}")
    if not no_latest:
        click.echo("Tagged:   latest")
        # Use /latest in download URL instead of hash ref
        display_download_url = f"/artifacts/{artifact_path}/latest"
    else:
        display_download_url = data["download_url"]
    click.echo(f"Download: {ctx.server}{display_download_url}")

    if not source_uri:
        click.echo()
        click.echo("Info: No --source-uri provided. Consider adding provenance metadata.")
