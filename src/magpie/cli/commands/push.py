"""Push command for uploading artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    import httpx

from magpie.cli import CLIContext


@click.command()
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--to", "artifact_path", required=True, help="Artifact path on server.")
@click.option("--source-uri", help="Source URI for provenance tracking.")
@click.option("--no-latest", is_flag=True, help="Don't auto-tag as latest.")
@click.pass_obj
def push(
    ctx: CLIContext,
    file: Path,
    artifact_path: str,
    source_uri: str | None,
    no_latest: bool,
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

    # Upload file
    with ctx.get_client() as client:
        with file.open("rb") as f:
            files = {"file": (file.name, f, "application/octet-stream")}

            if ctx.debug:
                click.echo(f"Uploading {file} to {artifact_path}...", err=True)

            response = client.post(
                f"/api/v1/upload/{artifact_path}",
                files=files,
                params=params if params else None,
            )

        if response.status_code != 200:
            _handle_error(response)

        data = response.json()

    # Display results
    if data.get("is_duplicate"):
        click.echo(f"Duplicate: {data['hash']}")
    else:
        click.echo(f"Uploaded: {data['hash']}")

    click.echo(f"Hash ref: {data['hash_ref']}")
    click.echo(f"Download: {ctx.server}{data['download_url']}")


def _handle_error(response: "httpx.Response") -> None:
    """Handle HTTP error responses."""
    try:
        detail = response.json().get("detail", response.text)
    except Exception:
        detail = response.text

    raise click.ClickException(f"Upload failed ({response.status_code}): {detail}")
