"""Get command for downloading artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path

import click

from magpie.cli import CLIContext
from magpie.cli.commands.parse import ParseError, parse_artifact_ref
from magpie.cli.progress import transfer_progress
from magpie.cli.utils import handle_http_error
from magpie.storage.hash import compute_hash


@click.command()
@click.argument("artifact_ref")
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Output file path.")
@click.option("--no-verify", is_flag=True, help="Skip SHA-256 verification.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress progress output.")
@click.option(
    "--force", "-f", is_flag=True, help="Overwrite existing output file without prompting."
)
@click.pass_obj
def get(
    ctx: CLIContext,
    artifact_ref: str,
    output: Path | None,
    no_verify: bool,
    quiet: bool,
    force: bool,
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
        # Fetch metadata to get hash and size for verification/progress
        if ctx.debug:
            click.echo(f"Fetching metadata for {parsed.path}:{parsed.ref}...", err=True)

        info_response = client.get(f"/api/v1/artifacts/{parsed.path}/{parsed.ref}/info")

        if info_response.status_code == 404:
            raise click.ClickException(f"Artifact not found: {parsed.path}:{parsed.ref}")
        if info_response.status_code != 200:
            handle_http_error(info_response, "Metadata fetch", ctx.token)

        info = info_response.json()
        expected_hash = info["hash"]
        hash_ref = info["hash_ref"]
        file_size = info.get("size", 0)

        if ctx.debug:
            click.echo(f"Hash: {expected_hash}", err=True)
            click.echo(f"Hash ref: {hash_ref}", err=True)
            click.echo(f"Size: {file_size} bytes", err=True)

        # Determine output path early so we can check for existing file before download
        if output is None:
            # Derive from artifact path (use last component)
            output = Path(parsed.path.split("/")[-1])

        # Check if output file exists before downloading to avoid wasting bandwidth
        if output.exists():
            # If local file has same hash as remote, skip download entirely
            # Use compute_hash for streaming hash computation (handles large files)
            local_hash = compute_hash(output)
            if local_hash == expected_hash:
                click.echo(f"File already exists with matching hash: {output}")
                return

            # File exists but has different hash - require --force
            if not force:
                raise click.ClickException(
                    f"Output file already exists: {output}\n"
                    "Use --force to overwrite existing files."
                )

        # Download the artifact
        # Hash refs use blobs/ subdirectory, tags are symlinks at root
        if hash_ref.startswith("@"):
            download_url = f"/artifacts/{parsed.path}/blobs/{hash_ref.lstrip('@')}"
        else:
            download_url = f"/artifacts/{parsed.path}/{hash_ref}"

        if ctx.debug:
            click.echo(f"Downloading from {download_url}...", err=True)

        # Use streaming download with progress
        chunks: list[bytes] = []
        with client.stream("GET", download_url) as response:
            if response.status_code == 404:
                raise click.ClickException(f"Artifact blob not found: {hash_ref}")
            if response.status_code != 200:
                # Read response body for error message
                response.read()
                handle_http_error(response, "Download", ctx.token)

            # Get content length from header if available (may be more accurate)
            content_length = response.headers.get("content-length")
            if content_length:
                file_size = int(content_length)

            with transfer_progress("Downloading", file_size, quiet=quiet) as (progress, task_id):
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    if progress is not None and task_id is not None:
                        progress.update(task_id, advance=len(chunk))

        content = b"".join(chunks)

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

    # Write file
    output.write_bytes(content)
    click.echo(f"Downloaded: {output}")
