"""Get command for downloading artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    import httpx

from magpie.cli import CLIContext
from magpie.cli.commands.parse import ParseError, parse_artifact_ref


@click.command()
@click.argument("artifact_ref")
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Output file path.")
@click.option("--no-verify", is_flag=True, help="Skip SHA-256 verification.")
@click.pass_obj
def get(
    ctx: CLIContext,
    artifact_ref: str,
    output: Path | None,
    no_verify: bool,
) -> None:
    """Download an artifact from the server.

    ARTIFACT_REF is the artifact path and optional ref (path:ref format).
    If no ref is specified, "latest" is used.

    Examples:

        magpie get images/ubuntu:latest

        magpie get images/ubuntu:@abc12345 -o ubuntu.tar.gz

        magpie get builds/app --no-verify
    """
    if not ctx.server:
        raise click.ClickException("No server configured. Use --server or set MAGPIE_SERVER.")

    try:
        parsed = parse_artifact_ref(artifact_ref)
    except ParseError as e:
        raise click.ClickException(str(e))

    with ctx.get_client() as client:
        # Fetch metadata to get hash for verification
        if ctx.debug:
            click.echo(f"Fetching metadata for {parsed.path}:{parsed.ref}...", err=True)

        info_response = client.get(f"/api/v1/artifacts/{parsed.path}/{parsed.ref}/info")

        if info_response.status_code == 404:
            raise click.ClickException(f"Artifact not found: {parsed.path}:{parsed.ref}")
        if info_response.status_code != 200:
            _handle_error(info_response, "metadata fetch")

        info = info_response.json()
        expected_hash = info["hash"]
        hash_ref = info["hash_ref"]

        if ctx.debug:
            click.echo(f"Hash: {expected_hash}", err=True)
            click.echo(f"Hash ref: {hash_ref}", err=True)

        # Download the artifact
        # Hash refs use blobs/ subdirectory, tags are symlinks at root
        if hash_ref.startswith("@"):
            download_url = f"/artifacts/{parsed.path}/blobs/{hash_ref.lstrip('@')}"
        else:
            download_url = f"/artifacts/{parsed.path}/{hash_ref}"

        if ctx.debug:
            click.echo(f"Downloading from {download_url}...", err=True)

        download_response = client.get(download_url)

        if download_response.status_code == 404:
            raise click.ClickException(f"Artifact blob not found: {hash_ref}")
        if download_response.status_code != 200:
            _handle_error(download_response, "download")

        content = download_response.content

    # Verify hash unless --no-verify
    if not no_verify:
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != expected_hash:
            raise click.ClickException(
                f"Hash mismatch! Expected {expected_hash}, got {actual_hash}. "
                "File may be corrupted. Use --no-verify to skip verification."
            )
        if ctx.debug:
            click.echo("Hash verified.", err=True)

    # Determine output path
    if output is None:
        # Derive from artifact path (use last component)
        output = Path(parsed.path.split("/")[-1])

    # Write file
    output.write_bytes(content)
    click.echo(f"Downloaded: {output}")


def _handle_error(response: "httpx.Response", operation: str) -> None:
    """Handle HTTP error responses."""
    try:
        detail = response.json().get("detail", response.text)
    except Exception:
        detail = response.text

    raise click.ClickException(f"{operation} failed ({response.status_code}): {detail}")
