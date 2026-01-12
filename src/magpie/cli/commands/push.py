"""Push command for uploading artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

import click

if TYPE_CHECKING:
    import httpx
    from rich.progress import Progress, TaskID

from magpie.cli import CLIContext
from magpie.cli.progress import transfer_progress
from magpie.cli.utils import handle_http_error


class ProgressFileWrapper:
    """File wrapper that reports read progress to rich progress bar."""

    def __init__(
        self,
        file: BinaryIO,
        progress: "Progress | None",
        task_id: "TaskID | None",
    ) -> None:
        self._file = file
        self._progress = progress
        self._task_id = task_id

    def read(self, size: int = -1) -> bytes:
        """Read bytes and update progress."""
        data = self._file.read(size)
        if self._progress is not None and self._task_id is not None:
            self._progress.update(self._task_id, advance=len(data))
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        """Seek in file (needed for multipart encoding)."""
        return self._file.seek(offset, whence)

    def tell(self) -> int:
        """Return current position."""
        return self._file.tell()


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
        raise click.ClickException("No server configured. Use --server or set MAGPIE_SERVER.")

    # Build query params
    params: dict[str, str] = {}
    if source_uri:
        params["source_uri"] = source_uri
    if no_latest:
        params["no_latest"] = "true"

    file_size = file.stat().st_size

    if ctx.debug:
        click.echo(f"Uploading {file} ({file_size} bytes) to {artifact_path}...", err=True)

    # Pre-flight auth check before showing progress bar.
    # This prevents the misleading UX where progress completes before auth error appears.
    with ctx.get_client() as client:
        preflight = client.head(f"/api/v1/upload/{artifact_path}")
        if preflight.status_code in (401, 403):
            handle_http_error(preflight, "Upload", ctx.token)

    # Upload file with progress (auth already verified)
    with transfer_progress("Uploading", file_size, quiet=quiet) as (progress, task_id):
        with ctx.get_client() as client:
            with file.open("rb") as f:
                wrapped_file = ProgressFileWrapper(f, progress, task_id)
                files = {"file": (file.name, wrapped_file, "application/octet-stream")}

                response = client.post(
                    f"/api/v1/upload/{artifact_path}",
                    files=files,
                    params=params if params else None,
                )

            if response.status_code != 200:
                handle_http_error(response, "Upload", ctx.token)

            data = response.json()

    # Display results
    if data.get("is_duplicate"):
        click.echo(f"Duplicate: {data['hash']}")
    else:
        click.echo(f"Uploaded: {data['hash']}")

    click.echo(f"Hash ref: {data['hash_ref']}")
    if not no_latest:
        click.echo("Tagged:   latest")
        # Use /latest in download URL instead of hash ref
        download_url = f"/artifacts/{artifact_path}/latest"
    else:
        download_url = data["download_url"]
    click.echo(f"Download: {ctx.server}{download_url}")

    if not source_uri:
        click.echo()
        click.echo("Info: No --source-uri provided. Consider adding provenance metadata.")
